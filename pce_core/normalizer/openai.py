"""PCE Core – OpenAI Chat Completions normalizer.

Handles:
- POST /v1/chat/completions  (request: messages array, response: choices array)
- Streaming responses (assembled from SSE chunks) — best-effort

Request body example:
{
  "model": "gpt-4",
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello"}
  ]
}

Response body example:
{
  "choices": [
    {"message": {"role": "assistant", "content": "Hi there!"}}
  ],
  "model": "gpt-4",
  "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
}
"""

import json
import logging
import re
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.openai")

_CHAT_PATHS = {
    "/v1/chat/completions",
    "/chat/completions",
}

# Regex for path variants: /api/v0/chat/completions, /backend-api/conversation, etc.
_CHAT_PATH_RE = re.compile(
    r"chat[/_-]?completions"
    r"|/api/conversation"
    r"|/backend-api/conversation"
    r"|/api/append-message"         # Claude web
    r"|/generateContent"             # Gemini
    r"|/streamGenerateContent",      # Gemini streaming
    re.IGNORECASE,
)

# All known hosts where OpenAI-compatible format is used (API + web UI)
_COMPATIBLE_HOSTS = {
    # OpenAI
    "api.openai.com", "chatgpt.com", "chat.openai.com",
    # DeepSeek
    "api.deepseek.com", "chat.deepseek.com",
    # Groq / Together / Fireworks / OpenRouter
    "api.groq.com", "api.together.xyz", "api.fireworks.ai", "openrouter.ai",
    # Perplexity / Mistral / xAI
    "api.perplexity.ai", "www.perplexity.ai",
    "api.mistral.ai", "chat.mistral.ai",
    "api.x.ai", "grok.com",
    # Zhipu / ChatGLM (uses OpenAI-compatible format)
    "open.bigmodel.cn", "chat.z.ai", "chatglm.cn", "chat.zhipuai.cn", "maas.aminer.cn",
    # Moonshot / Kimi
    "api.moonshot.cn", "kimi.moonshot.cn",
    # Local
    "localhost", "127.0.0.1",
}


class OpenAIChatNormalizer(BaseNormalizer):
    """Normalizes OpenAI-compatible chat completions (also works for DeepSeek, Groq, Together, etc.)."""

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        # Exact path match
        if path in _CHAT_PATHS:
            return True
        # Regex path match (catches /api/v0/chat/completions etc.)
        if _CHAT_PATH_RE.search(path):
            return True
        # Known OpenAI-compatible host (API or web UI)
        if host in _COMPATIBLE_HOSTS:
            return True
        # Known provider
        if provider in ("openai", "groq", "together", "fireworks", "openrouter", "zhipu", "moonshot"):
            return True
        return False

    def normalize(
        self,
        request_body: str,
        response_body: str,
        *,
        provider: str,
        host: str,
        path: str,
        model_name: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> Optional[NormalizedResult]:
        messages: list[NormalizedMessage] = []

        # --- Parse request messages ---
        req_data = _safe_json(request_body)
        if req_data is None:
            return None

        req_messages = req_data.get("messages", [])
        if not isinstance(req_messages, list):
            return None

        model = model_name or req_data.get("model")

        for msg in req_messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            text, attachments = _extract_rich_content(msg)
            cj = json.dumps({"attachments": attachments}, ensure_ascii=False) if attachments else None
            messages.append(NormalizedMessage(
                role=role,
                content_text=text,
                content_json=cj,
                model_name=model if role == "assistant" else None,
                ts=created_at,
            ))

        # --- Parse response ---
        resp_data = _safe_json(response_body)

        # SSE fallback: if response isn't valid JSON, try assembling SSE chunks
        if resp_data is None and response_body:
            from .sse import is_sse_text, assemble_sse_response
            if is_sse_text(response_body):
                resp_data = assemble_sse_response(response_body)

        if resp_data and isinstance(resp_data, dict):
            resp_model = resp_data.get("model", model)
            usage = resp_data.get("usage", {})
            completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None

            choices = resp_data.get("choices", [])
            if isinstance(choices, list):
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    msg = choice.get("message") or choice.get("delta", {})
                    if not isinstance(msg, dict):
                        continue
                    role = msg.get("role", "assistant")
                    text, attachments = _extract_rich_content(msg)
                    if text:
                        cj = json.dumps({"attachments": attachments}, ensure_ascii=False) if attachments else None
                        messages.append(NormalizedMessage(
                            role=role,
                            content_text=text,
                            content_json=cj,
                            model_name=resp_model,
                            token_estimate=completion_tokens,
                            ts=created_at,
                        ))

        if not messages:
            return None

        # Derive tool_family from host
        tool_family = _tool_family(host, provider)

        # Session key: conversation_id groups all messages from one chat
        session_key = (
            req_data.get("conversation_id")
            or req_data.get("session_id")
        )

        # Title hint: prefer explicit title from browser ext, else first user message
        title_hint = req_data.get("title")
        if not title_hint:
            for m in messages:
                if m.role == "user" and m.content_text:
                    title_hint = m.content_text[:100]
                    break

        return NormalizedResult(
            provider=provider or "openai",
            tool_family=tool_family,
            model_name=model,
            session_key=session_key,
            title_hint=title_hint,
            messages=messages,
        )


def _safe_json(text: str) -> Optional[dict]:
    """Parse JSON, returning None on failure."""
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_rich_content(msg: dict) -> tuple[Optional[str], list[dict]]:
    """Extract text and rich-content attachments from an OpenAI message.

    Returns ``(text, attachments)`` where *attachments* is a list of dicts
    describing non-text content (images, tool calls, audio, files, etc.).

    Attachment types:
    - image_url      — user-uploaded or inline image
    - image_generation — DALL-E generated image (in response)
    - tool_call      — function/tool invocation by assistant
    - tool_result    — result returned for a tool call (role=tool)
    - audio          — audio input or output
    - file           — file attachment reference
    - code_output    — code interpreter sandbox output
    - citation       — search/browse citation
    """
    attachments: list[dict] = []
    text_parts: list[str] = []
    content = msg.get("content")

    def _consume_internal_content(obj: dict) -> bool:
        ctype = obj.get("content_type", "")
        parts = obj.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, str):
                    if part:
                        text_parts.append(part)
                elif isinstance(part, dict):
                    part_type = part.get("content_type", "")
                    if part_type == "image_asset_pointer" or "asset_pointer" in part:
                        asset_pointer = part.get("asset_pointer", part.get("url", ""))
                        file_id = ""
                        if isinstance(asset_pointer, str) and asset_pointer.startswith("sediment://file_"):
                            file_id = asset_pointer.split("sediment://", 1)[-1]
                        attachments.append({
                            "type": "image_url",
                            "url": asset_pointer,
                            "file_id": file_id,
                            "size": part.get("size_bytes", 0),
                        })
                        text_parts.append("[Image]")
                    elif "text" in part:
                        text_parts.append(str(part.get("text", "")))
                    else:
                        attachments.append({
                            "type": part_type or "unknown_part",
                            "raw": _truncate(json.dumps(part, ensure_ascii=False), 2000),
                        })
                        text_parts.append(f"[{part_type or 'unknown'}]")
            return True
        if ctype == "text":
            text_val = obj.get("text")
            if isinstance(text_val, str) and text_val:
                text_parts.append(text_val)
                return True
        return False

    # --- content field --------------------------------------------------------
    if isinstance(content, str):
        stripped = content.strip()
        parsed_internal = None
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed_internal = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                try:
                    import ast
                    parsed_internal = ast.literal_eval(stripped)
                except Exception:
                    parsed_internal = None
        if isinstance(parsed_internal, dict) and (
            "parts" in parsed_internal or "content_type" in parsed_internal
        ):
            if not _consume_internal_content(parsed_internal):
                text_parts.append(content)
        else:
            text_parts.append(content)
    elif isinstance(content, dict):
        if not _consume_internal_content(content) and content:
            text_parts.append(str(content))
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif not isinstance(part, dict):
                continue
            else:
                ptype = part.get("type", "")
                if ptype == "text":
                    text_parts.append(part.get("text", ""))
                    # Citations embedded inside text part annotations
                    for ann in part.get("annotations", []):
                        if isinstance(ann, dict):
                            attachments.append({
                                "type": "citation",
                                "url": ann.get("url", ann.get("url_citation", {}).get("url", "")),
                                "title": ann.get("title", ann.get("url_citation", {}).get("title", "")),
                                "text": ann.get("text", ""),
                            })
                elif ptype == "image_url":
                    img = part.get("image_url", {})
                    url = img.get("url", "") if isinstance(img, dict) else str(img)
                    # Truncate data-URIs to avoid bloating DB
                    stored_url = url if not url.startswith("data:") else url[:200] + "...[truncated]"
                    attachments.append({
                        "type": "image_url",
                        "url": stored_url,
                        "detail": img.get("detail", "auto") if isinstance(img, dict) else "auto",
                    })
                    text_parts.append("[Image]")
                elif ptype == "image_file":
                    fobj = part.get("image_file", {})
                    attachments.append({
                        "type": "image_url",
                        "file_id": fobj.get("file_id", "") if isinstance(fobj, dict) else "",
                    })
                    text_parts.append("[Image file]")
                elif ptype == "input_audio":
                    audio = part.get("input_audio", {})
                    attachments.append({
                        "type": "audio",
                        "format": audio.get("format", "") if isinstance(audio, dict) else "",
                    })
                    text_parts.append("[Audio input]")
                elif ptype == "refusal":
                    text_parts.append(part.get("refusal", "[Refused]"))
                else:
                    # Unknown part type — preserve as attachment
                    attachments.append({"type": ptype, "raw": _truncate(json.dumps(part, ensure_ascii=False), 2000)})
                    text_parts.append(f"[{ptype}]")
    elif content is not None:
        text_parts.append(str(content))

    # --- tool_calls (assistant requesting tool use) ---------------------------
    for tc in msg.get("tool_calls", []):
        if not isinstance(tc, dict):
            continue
        func = tc.get("function", {})
        name = func.get("name", "?") if isinstance(func, dict) else "?"
        args = func.get("arguments", "") if isinstance(func, dict) else ""
        attachments.append({
            "type": "tool_call",
            "id": tc.get("id", ""),
            "name": name,
            "arguments": _truncate(args, 4000),
        })
        text_parts.append(f"[Tool call: {name}]")

    # --- legacy function_call -------------------------------------------------
    fc = msg.get("function_call")
    if isinstance(fc, dict):
        attachments.append({
            "type": "tool_call",
            "name": fc.get("name", ""),
            "arguments": _truncate(fc.get("arguments", ""), 4000),
        })
        text_parts.append(f"[Tool call: {fc.get('name', '?')}]")

    # --- role=tool result -----------------------------------------------------
    if msg.get("role") == "tool":
        attachments.append({
            "type": "tool_result",
            "tool_call_id": msg.get("tool_call_id", ""),
            "name": msg.get("name", ""),
            "content": _truncate(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False) if content else "", 8000),
        })

    # --- audio output (assistant) ---------------------------------------------
    audio = msg.get("audio")
    if isinstance(audio, dict):
        transcript = audio.get("transcript", "")
        attachments.append({
            "type": "audio",
            "transcript": _truncate(transcript, 4000),
        })
        if transcript and transcript not in "\n".join(text_parts):
            text_parts.append(f"[Audio: {transcript[:100]}]")

    text = "\n".join(text_parts) if text_parts else None
    if attachments:
        deduped: list[dict] = []
        seen: set[str] = set()
        for att in attachments:
            key = json.dumps(att, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(att)
        attachments = deduped
    return text, attachments


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit] + "...[truncated]"


def _tool_family(host: str, provider: str) -> str:
    """Derive tool_family from host."""
    if host in ("localhost", "127.0.0.1"):
        return "local-model"
    if "openai" in host:
        return "api-direct"
    if "deepseek" in host:
        return "api-direct"
    if "groq" in host:
        return "api-direct"
    if "together" in host:
        return "api-direct"
    if "fireworks" in host:
        return "api-direct"
    if "openrouter" in host:
        return "openrouter"
    return f"api-{provider}"
