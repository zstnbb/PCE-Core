# SPDX-License-Identifier: Apache-2.0
"""PCE Proxy – Heuristic AI traffic detection.

Used in CaptureMode.SMART to identify AI API calls that are NOT on the
static allowlist. Checks domain patterns, request body signatures, and
response body signatures.

Returns a confidence level: "high", "medium", or None (not AI).
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger("pce.heuristic")

# ---------------------------------------------------------------------------
# Rule 1: Domain pattern matching (subdomain / keyword patterns)
# ---------------------------------------------------------------------------

DOMAIN_PATTERNS: list[re.Pattern] = [
    # Known AI provider subdomains
    re.compile(r".*\.openai\.com$"),
    re.compile(r".*\.anthropic\.com$"),
    re.compile(r".*\.googleapis\.com$"),
    re.compile(r".*\.deepseek\.com$"),
    re.compile(r".*\.mistral\.ai$"),
    re.compile(r".*\.cohere\.com$"),
    # Generic AI-related domain keywords
    re.compile(r".*api.*\.ai$"),
    re.compile(r".*api.*\.ml$"),
    re.compile(r".*chat\..*\.ai$"),
    re.compile(r".*llm.*\..*$"),
    re.compile(r".*\.ai\..*\.com$"),
    # Common AI endpoint patterns
    re.compile(r".*inference.*\..*$"),
    re.compile(r".*completion.*\..*$"),
]


def match_domain_pattern(host: str) -> Optional[str]:
    """Check if host matches known AI domain patterns. Returns pattern or None."""
    for pat in DOMAIN_PATTERNS:
        if pat.match(host):
            return pat.pattern
    return None


# ---------------------------------------------------------------------------
# Rule 2: Request body heuristics (JSON POST bodies)
# ---------------------------------------------------------------------------

# High confidence: these field combinations strongly indicate an AI API call
HIGH_CONFIDENCE_REQUEST = [
    {"model", "messages"},          # OpenAI / Anthropic chat completions
    {"model", "prompt"},            # Legacy completions
    {"model", "input"},             # Embeddings / moderation
    {"prompt", "max_tokens"},       # Various completion APIs
    {"model", "messages", "stream"},# Streaming chat
]

# Medium confidence: partial matches
MEDIUM_CONFIDENCE_REQUEST = [
    {"messages"},                    # Messages without explicit model
    {"prompt", "temperature"},       # Prompt with sampling params
    {"model", "content"},            # Some API variants
    {"inputs"},                      # HuggingFace inference API
]

# Individual fields that add confidence
AI_REQUEST_FIELDS = {
    "model", "messages", "prompt", "max_tokens", "temperature",
    "top_p", "stream", "stop", "system", "tools", "tool_choice",
    "response_format", "n", "presence_penalty", "frequency_penalty",
    "logprobs", "top_logprobs", "seed",
}


def check_request_body(body_bytes: bytes) -> tuple[Optional[str], Optional[str]]:
    """Analyse request body for AI API patterns.

    Returns (confidence, reason) where confidence is "high", "medium", or None.
    """
    if not body_bytes or len(body_bytes) < 10:
        return None, None

    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None

    if not isinstance(data, dict):
        return None, None

    keys = set(data.keys())

    # Check high-confidence combinations
    for combo in HIGH_CONFIDENCE_REQUEST:
        if combo.issubset(keys):
            return "high", f"request_fields:{'+'.join(sorted(combo))}"

    # Check medium-confidence combinations
    for combo in MEDIUM_CONFIDENCE_REQUEST:
        if combo.issubset(keys):
            return "medium", f"request_fields:{'+'.join(sorted(combo))}"

    # Count AI-related fields
    ai_field_count = len(keys & AI_REQUEST_FIELDS)
    if ai_field_count >= 3:
        return "medium", f"request_ai_fields:{ai_field_count}"

    return None, None


# ---------------------------------------------------------------------------
# Rule 3: Response body / header heuristics
# ---------------------------------------------------------------------------

# JSON response fields that indicate AI API responses
HIGH_CONFIDENCE_RESPONSE_FIELDS = [
    {"choices"},                     # OpenAI completions
    {"content", "role"},             # Anthropic message response
    {"completion"},                  # Legacy completion response
    {"generations"},                 # Cohere
]

MEDIUM_CONFIDENCE_RESPONSE_FIELDS = [
    {"generated_text"},              # HuggingFace
    {"output"},                      # Various
    {"result", "model"},             # Some APIs
]


def check_response_body(body_bytes: bytes, content_type: str = "") -> tuple[Optional[str], Optional[str]]:
    """Analyse response body/headers for AI API patterns.

    Returns (confidence, reason).
    """
    # SSE streaming with AI-like content
    if "text/event-stream" in content_type:
        if body_bytes:
            snippet = body_bytes[:2000].decode("utf-8", errors="replace")
            if '"choices"' in snippet or '"delta"' in snippet or '"content_block"' in snippet:
                return "high", "sse_stream_with_ai_fields"
            if '"data"' in snippet and ('"model"' in snippet or '"text"' in snippet):
                return "medium", "sse_stream_with_model"

    if not body_bytes or len(body_bytes) < 5:
        return None, None

    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Could be SSE text, check for AI patterns in raw text
        snippet = body_bytes[:2000].decode("utf-8", errors="replace")
        if '"choices"' in snippet and '"message"' in snippet:
            return "high", "raw_text_choices_message"
        if '"content_block"' in snippet:
            return "high", "raw_text_content_block"
        return None, None

    if not isinstance(data, dict):
        return None, None

    keys = set(data.keys())

    for combo in HIGH_CONFIDENCE_RESPONSE_FIELDS:
        if combo.issubset(keys):
            return "high", f"response_fields:{'+'.join(sorted(combo))}"

    for combo in MEDIUM_CONFIDENCE_RESPONSE_FIELDS:
        if combo.issubset(keys):
            return "medium", f"response_fields:{'+'.join(sorted(combo))}"

    # Check nested: data.choices[0].message / data.choices[0].delta
    if "choices" in data and isinstance(data.get("choices"), list) and len(data["choices"]) > 0:
        choice = data["choices"][0]
        if isinstance(choice, dict) and ("message" in choice or "delta" in choice or "text" in choice):
            return "high", "response_choices_with_content"

    return None, None


# ---------------------------------------------------------------------------
# Combined heuristic check
# ---------------------------------------------------------------------------

def detect_ai_request(
    host: str,
    path: str,
    method: str,
    request_body: bytes,
) -> tuple[Optional[str], list[str]]:
    """Run all request-phase heuristics.

    Returns (confidence, reasons) where confidence is "high", "medium", or None.
    """
    reasons = []
    max_confidence = None

    # Domain pattern
    pattern = match_domain_pattern(host)
    if pattern:
        reasons.append(f"domain_pattern:{pattern}")
        max_confidence = "medium"

    # Path hints
    path_lower = path.lower()
    ai_path_keywords = [
        "/v1/chat/completions", "/v1/completions", "/v1/embeddings",
        "/v1/messages", "/chat/completions", "/completions",
        "/v1/engines/", "/inference", "/generate", "/predict",
        "/api/generate", "/api/chat",
    ]
    for kw in ai_path_keywords:
        if kw in path_lower:
            reasons.append(f"path:{kw}")
            max_confidence = _upgrade_confidence(max_confidence, "medium")
            break

    # Request body (only for POST/PUT)
    if method.upper() in ("POST", "PUT") and request_body:
        body_conf, body_reason = check_request_body(request_body)
        if body_conf:
            reasons.append(body_reason)
            max_confidence = _upgrade_confidence(max_confidence, body_conf)

    return max_confidence, reasons


def detect_ai_response(
    response_body: bytes,
    content_type: str = "",
) -> tuple[Optional[str], list[str]]:
    """Run response-phase heuristics.

    Returns (confidence, reasons).
    """
    reasons = []
    conf, reason = check_response_body(response_body, content_type)
    if conf:
        reasons.append(reason)
    return conf, reasons


def _upgrade_confidence(current: Optional[str], new: str) -> str:
    """Return the higher confidence level."""
    order = {"high": 2, "medium": 1}
    if current is None:
        return new
    if order.get(new, 0) > order.get(current, 0):
        return new
    return current
