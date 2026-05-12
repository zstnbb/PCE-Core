# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor.classifier — FailureKind 9-value enum + heuristic.

The closed enum (per ADR-017 §3.3) is the canonical contract between
the conductor and the calling agent: every failure resolves to exactly
one of these values, with deterministic ``severity`` and a small set
of structured fields. Anything that doesn't fit goes to ``UNKNOWN``
explicitly — there is no fall-through.

The classifier is a heuristic — it inspects:

- the run's ``stdout`` / ``stderr`` (truncated)
- the run's ``exit_code``
- the run's ``elapsed_s`` (for RACE_TIMEOUT detection)
- (optionally) the canary diff for the same case (SCHEMA_DRIFT detection)

It is intentionally **conservative**: ambiguous failures land in
``UNKNOWN`` rather than guessing — agent reviewers (Cascade, Claude
Code, etc.) handle the long tail with their own context, while the
classifier owns the 80 % of recurrent kinds.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class FailureKind(str, Enum):
    """Closed enum (9 values) per ADR-017 §3.3."""

    LOGIN_WALL = "LOGIN_WALL"                    # adapter detected the login page
    UI_SELECTOR_MISS = "UI_SELECTOR_MISS"        # browser/desktop: DOM/UIA element not found
    NETWORK_NOISE_MISS = "NETWORK_NOISE_MISS"    # captured but filtered by noise list
    SCHEMA_DRIFT = "SCHEMA_DRIFT"                # payload field disappeared / type changed
    URL_PATTERN_DRIFT = "URL_PATTERN_DRIFT"      # endpoint path changed
    CONTENT_BLOCK_UNKNOWN = "CONTENT_BLOCK_UNKNOWN"  # new content_block.type / role
    RACE_TIMEOUT = "RACE_TIMEOUT"                # verifier timed out waiting
    INFRA = "INFRA"                              # PCE Core not running, port busy, etc.
    UNKNOWN = "UNKNOWN"                          # cannot classify — human triage required


class Severity(str, Enum):
    """Per ADR-017 §3.3: severity ladder."""

    HARD = "hard"   # existing field gone / type changed / URL replaced
    SOFT = "soft"   # new field / new enum value (lossy but not breaking)
    INFO = "info"   # count / timing drift only


@dataclass
class FailureRecord:
    """JSON-serialisable failure classification."""

    kind: FailureKind
    severity: Severity
    field_path: Optional[str] = None
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    hint: Optional[str] = None          # human-readable repair hint
    evidence_excerpt: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["severity"] = self.severity.value
        return d


# ---------------------------------------------------------------------------
# Heuristic patterns (conservative — designed to favour false-negatives over
# false-positives so an agent reviewer always sees structured data when the
# classifier is confident, and ``UNKNOWN`` when it's not).
# ---------------------------------------------------------------------------

# Each entry: (regex, kind, severity, hint).
_PATTERNS: tuple[tuple[re.Pattern[str], FailureKind, Severity, str], ...] = (
    # ---- INFRA — detect first so transport/setup errors don't get
    # mis-classified as UI_SELECTOR_MISS or RACE_TIMEOUT ----
    (
        re.compile(r"(?i)connection\s+refused|address\s+already\s+in\s+use|"
                   r"port\s+\d+\s+is\s+(in\s+use|already\s+bound)|"
                   r"could\s+not\s+connect\s+to\s+pce\s+core|"
                   r"pce[_\s-]?core\s+not\s+(reachable|running|started)|"
                   r"no\s+such\s+(file|directory).*\.db"),
        FailureKind.INFRA, Severity.HARD,
        "PCE Core / port / DB infrastructure unhealthy — start pce_core.server first.",
    ),

    # ---- LOGIN_WALL ----
    (
        re.compile(r"(?i)login\s+(wall|required|page)|sign\s+in\s+to\s+continue|"
                   r"401\s+unauthorized|please\s+authenticate"),
        FailureKind.LOGIN_WALL, Severity.HARD,
        "Site is showing the login page; refresh credentials or log in manually.",
    ),

    # ---- UI_SELECTOR_MISS ----
    (
        re.compile(r"(?i)NoSuchElement|element\s+not\s+(found|located|interactable)|"
                   r"selector.*(no\s+match|empty)|"
                   r"unable\s+to\s+locate\s+element|"
                   r"timed\s+out\s+waiting\s+for\s+selector"),
        FailureKind.UI_SELECTOR_MISS, Severity.HARD,
        "DOM/UIA selector did not match — site UI likely changed.",
    ),

    # ---- URL_PATTERN_DRIFT ----
    (
        re.compile(r"(?i)endpoint\s+\S+\s+returned\s+404|"
                   r"path\s+changed|url\s+pattern\s+drift|"
                   r"unrecognised\s+path|unmatched\s+route"),
        FailureKind.URL_PATTERN_DRIFT, Severity.HARD,
        "Endpoint path no longer responds — verify _PATHS in normalizer.",
    ),

    # ---- CONTENT_BLOCK_UNKNOWN ----
    (
        re.compile(r"(?i)unknown\s+(content[_\s]?block|block\s+type)|"
                   r"unrecognised\s+content_block\.type|"
                   r"unsupported\s+block.*\b(server_tool_use|web_search_tool_use|"
                   r"thinking_redacted)\b"),
        FailureKind.CONTENT_BLOCK_UNKNOWN, Severity.SOFT,
        "Provider added a new content_block.type — extend the elif chain in normalizer/<provider>.py.",
    ),

    # ---- SCHEMA_DRIFT ----
    (
        re.compile(r"(?i)KeyError:\s*['\"](?P<field>[^'\"]+)['\"]|"
                   r"validation\s+error.*field\s+required|"
                   r"required\s+field\s+missing|"
                   r"pydantic.*field\s+required|"
                   r"missing\s+key\s+['\"]?(?P<field2>[^'\"\s]+)"),
        FailureKind.SCHEMA_DRIFT, Severity.HARD,
        "Required field disappeared from upstream payload — widen normalizer / model.",
    ),

    # ---- NETWORK_NOISE_MISS ----
    (
        re.compile(r"(?i)noise[_\s-]?(filter|path)|"
                   r"capture\s+filtered\s+as\s+noise|"
                   r"_NOISE_PATH_PATTERNS\s+rejected"),
        FailureKind.NETWORK_NOISE_MISS, Severity.SOFT,
        "Capture intercepted but rejected by noise filter — review _NOISE_PATH_PATTERNS.",
    ),

    # ---- RACE_TIMEOUT — last because it's the broadest match ----
    (
        re.compile(r"(?i)timeout|timed\s+out|wait_for_session.*exhausted|"
                   r"never\s+observed|did\s+not\s+arrive\s+within"),
        FailureKind.RACE_TIMEOUT, Severity.SOFT,
        "Verifier waited but the expected event never arrived — could be load, could be regression.",
    ),
)


# Field-path extractor for SCHEMA_DRIFT / CONTENT_BLOCK_UNKNOWN.
_FIELD_RE: re.Pattern[str] = re.compile(
    r"(?:KeyError:|missing\s+key)\s*['\"]?(?P<field>[A-Za-z_][\w.\[\]\-]*)"
)


def classify_run(
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    elapsed_s: Optional[float] = None,
    timeout_s: Optional[int] = None,
    canary_diff: Optional[list[dict[str, Any]]] = None,
) -> FailureRecord:
    """Map a run's evidence to a single ``FailureRecord``.

    Resolution order (first match wins):

    1. ``exit_code == 0`` → caller should not be calling us; we return
       ``UNKNOWN`` because the run wasn't actually a failure.
    2. canary_diff with ``hard`` severity → SCHEMA_DRIFT
       canary_diff with ``soft`` severity (enum_extension) → CONTENT_BLOCK_UNKNOWN
    3. RACE_TIMEOUT detected by elapsed_s ≥ timeout_s
    4. Pattern match on stderr (then stdout)
    5. UNKNOWN
    """
    if exit_code == 0:
        return FailureRecord(
            kind=FailureKind.UNKNOWN,
            severity=Severity.INFO,
            hint="exit_code=0 — run was actually successful, classifier should not have been called.",
        )

    # -------- canary-driven: most reliable signal --------
    if canary_diff:
        # Pick the most severe drift entry to attribute the failure.
        for entry in canary_diff:
            sev = str(entry.get("severity", "")).lower()
            kind_str = str(entry.get("kind", ""))
            if sev == "hard":
                return FailureRecord(
                    kind=FailureKind.SCHEMA_DRIFT,
                    severity=Severity.HARD,
                    field_path=entry.get("field_path"),
                    expected=entry.get("expected"),
                    actual=entry.get("actual"),
                    hint=(f"Canary diff: {kind_str} on {entry.get('field_path')!r} "
                          f"is hard severity — required field missing or type changed."),
                )
            if sev == "soft" and kind_str == "enum_extension":
                return FailureRecord(
                    kind=FailureKind.CONTENT_BLOCK_UNKNOWN,
                    severity=Severity.SOFT,
                    field_path=entry.get("field_path"),
                    expected=entry.get("expected"),
                    actual=entry.get("actual"),
                    hint="Canary diff: new enum value detected — extend elif chain.",
                )

    # -------- elapsed_s vs timeout_s deterministic detection --------
    if elapsed_s is not None and timeout_s is not None:
        # Allow 5% slack — pytest timeouts often fire a hair early.
        if elapsed_s >= timeout_s * 0.95:
            return FailureRecord(
                kind=FailureKind.RACE_TIMEOUT,
                severity=Severity.SOFT,
                hint=(f"elapsed_s={elapsed_s:.1f} ≥ timeout_s={timeout_s} — "
                      f"verifier waited the full window."),
                extras={"elapsed_s": elapsed_s, "timeout_s": timeout_s},
            )

    # -------- pattern match: stderr first, stdout second --------
    haystack = f"{stderr}\n{stdout}"
    for pat, kind, sev, hint in _PATTERNS:
        m = pat.search(haystack)
        if not m:
            continue
        # Extract a field_path for SCHEMA_DRIFT/CONTENT_BLOCK_UNKNOWN where possible.
        field_path: Optional[str] = None
        if kind in (FailureKind.SCHEMA_DRIFT, FailureKind.CONTENT_BLOCK_UNKNOWN):
            fm = _FIELD_RE.search(haystack)
            if fm:
                field_path = fm.group("field")
        # Truncate excerpt to ~200 chars centred on the match.
        start = max(0, m.start() - 80)
        end = min(len(haystack), m.end() + 120)
        excerpt = haystack[start:end].replace("\n", " ").strip()
        return FailureRecord(
            kind=kind,
            severity=sev,
            field_path=field_path,
            hint=hint,
            evidence_excerpt=excerpt,
            extras={"matched_pattern": pat.pattern[:80]},
        )

    # -------- nothing matched: explicit UNKNOWN --------
    return FailureRecord(
        kind=FailureKind.UNKNOWN,
        severity=Severity.INFO,
        hint="Classifier saw no recognised pattern — manual triage required.",
        evidence_excerpt=(stderr or stdout)[:300],
    )
