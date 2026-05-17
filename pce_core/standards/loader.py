# SPDX-License-Identifier: Apache-2.0
"""Standards loader -- parse pce_core/standards/<id>.md into typed Standard.

Format (per ADR-006 Wave 4r §5.1):

    ---
    target_id: f1_chatgpt_web
    domain: chatgpt.com
    tier: S0
    last_validated: 2026-05-17
    validator: manual-w4r-draft-2026-05-17
    schema_version: 1
    ---

    # Capability
    <prose>

    # Expected user-visible behaviour
    1. ...
    2. ...

    # Critical DOM landmarks
    - "..." -- explanation
    - "..." -- explanation

    # Verification scripts
    - t01_basic_chat
    - t02_streaming_complete

    # Redundancy plan
    - L1 (extension capture): <what it captures, where>
    - L2 (proxy capture):     <...>
    - L3 (DOM capture):       <...>

    # Known instability surfaces
    - <surface>

    # Not-our-concern
    - <surface>

Parsing rules:
    - Frontmatter is required and is parsed by PyYAML
    - Sections are split on H1 (^# Heading$) inside the body
    - List sections (Expected behaviour, Landmarks, Verification, Redundancy,
      Instability, Not-our-concern) capture lines starting with '- ' or '1.'
    - Prose sections (Capability) capture all non-list lines until the next H1
    - Leg parsing accepts "L1 (label): description" or "L1: description"
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


# Where standards live, by default. Resolves relative to this module so it
# works regardless of cwd (orchestrator sandboxes have unpredictable cwds).
_STANDARDS_DIR = Path(__file__).resolve().parent


class StandardsError(Exception):
    """Raised when a standard file is malformed or missing required fields."""


class Leg(BaseModel):
    """One redundancy leg from the standard's Redundancy plan section.

    Each leg is an *independent* path that can prove the capability is
    working. Per ADR-006: a target is V-GREEN if ≥1 leg green; the system
    aims for ≥3 legs so a single source of drift can't bring it down.
    """

    name: str  # e.g. "L1"
    label: str = ""  # e.g. "extension capture"
    description: str

    @classmethod
    def parse(cls, raw: str) -> "Leg":
        """Parse 'L1 (extension capture): background.js intercepts ...' or
        'L1: ...' into a Leg. Raises StandardsError if it can't.
        """
        s = raw.strip().lstrip("-").strip()
        m = re.match(r"^(L\d+)(?:\s*\(([^)]*)\))?\s*:\s*(.+)$", s, re.DOTALL)
        if not m:
            raise StandardsError(f"can't parse leg line: {raw!r}")
        return cls(name=m.group(1), label=(m.group(2) or "").strip(),
                   description=m.group(3).strip())


class Standard(BaseModel):
    """A parsed target standard.

    Frozen so that downstream code (orchestrator briefing builder, verifier)
    can treat the parsed standard as an immutable snapshot for a run.
    """

    model_config = {"frozen": True}

    target_id: str
    domain: str
    tier: Literal["S0", "S1", "S2"]
    last_validated: date
    validator: str
    schema_version: int

    capability: str
    expected_behaviour: list[str] = Field(default_factory=list)
    landmarks: list[str] = Field(default_factory=list)
    verification_scripts: list[str] = Field(default_factory=list)
    redundancy_legs: list[Leg] = Field(default_factory=list)
    instability_surfaces: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)

    # Path on disk; useful so the briefing builder can copy the raw markdown
    # into the agent's working dir without re-rendering.
    source_path: Path | None = None

    @field_validator("schema_version")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"schema_version {v} not supported; expected 1")
        return v

    @field_validator("verification_scripts")
    @classmethod
    def _at_least_one_script(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("verification_scripts must list ≥1 probe case")
        return v

    @field_validator("redundancy_legs")
    @classmethod
    def _at_least_three_legs(cls, v: list[Leg]) -> list[Leg]:
        # ADR-006 §3.3 Layer 4: ≥3 independent legs is the V-GREEN guarantee.
        # We enforce it at load time so a regression in a standard file gets
        # caught the moment list_standards() runs, not at probe time.
        if len(v) < 3:
            raise ValueError(
                f"redundancy_legs needs ≥3 entries (got {len(v)}). "
                "ADR-006 §3.3 Layer 4 -- V-GREEN ladder requires 3 legs."
            )
        return v


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_HEADING_RE = re.compile(r"^# (.+?)\s*$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.+)$")


def _split_sections(body: str) -> dict[str, str]:
    """Split markdown body on H1 headings into {heading_text: section_body}."""
    sections: dict[str, str] = {}
    # Find all (start, end_of_heading_line, heading_text)
    matches = list(_HEADING_RE.finditer(body))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        heading = m.group(1).strip()
        sections[heading] = body[start:end].strip()
    return sections


def _extract_list(section_text: str) -> list[str]:
    """Pull bullet / numbered list items from a section. Lines that aren't list
    items are skipped (so trailing prose / notes inside a list section don't
    pollute the parsed list)."""
    items: list[str] = []
    for line in section_text.splitlines():
        m = _LIST_ITEM_RE.match(line)
        if m:
            items.append(m.group(1).strip())
    return items


def _section_lookup(sections: dict[str, str], *aliases: str) -> str:
    """Find first section whose heading matches any alias (case-insensitive)."""
    norm = {k.lower(): v for k, v in sections.items()}
    for a in aliases:
        if a.lower() in norm:
            return norm[a.lower()]
        # also try substring match (e.g. "Verification scripts (sources of truth ...)" )
        for k, v in norm.items():
            if k.startswith(a.lower()):
                return v
    return ""


def _parse_markdown(text: str, source_path: Path | None = None) -> Standard:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise StandardsError(
            f"missing frontmatter (---) in {source_path or '<text>'}"
        )
    meta_yaml, body = m.group(1), m.group(2)
    try:
        meta = yaml.safe_load(meta_yaml) or {}
    except yaml.YAMLError as e:
        raise StandardsError(f"bad YAML frontmatter: {e}") from e
    if not isinstance(meta, dict):
        raise StandardsError("frontmatter must be a YAML mapping")

    sections = _split_sections(body)

    capability = _section_lookup(sections, "Capability").strip()
    expected_behaviour = _extract_list(
        _section_lookup(sections, "Expected user-visible behaviour",
                        "Expected behaviour")
    )
    landmarks = _extract_list(
        _section_lookup(sections, "Critical DOM landmarks", "Landmarks")
    )
    verification = _extract_list(
        _section_lookup(sections, "Verification scripts", "Verification")
    )
    instability = _extract_list(
        _section_lookup(sections, "Known instability surfaces", "Instability")
    )
    out_of_scope = _extract_list(
        _section_lookup(sections, "Not-our-concern", "Out of scope")
    )

    raw_legs = _extract_list(
        _section_lookup(sections, "Redundancy plan", "Redundancy")
    )
    legs = [Leg.parse(rl) for rl in raw_legs]

    fields = {
        **meta,
        "capability": capability,
        "expected_behaviour": expected_behaviour,
        "landmarks": landmarks,
        "verification_scripts": verification,
        "redundancy_legs": legs,
        "instability_surfaces": instability,
        "out_of_scope": out_of_scope,
        "source_path": source_path,
    }
    try:
        return Standard.model_validate(fields)
    except ValidationError as e:
        raise StandardsError(
            f"validation failed for {source_path or '<text>'}: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_standard(target_id: str, standards_dir: Path | None = None) -> Standard:
    """Load a single standard by target_id (e.g. 'f1_chatgpt_web').

    Raises StandardsError if not found or malformed.
    """
    d = standards_dir or _STANDARDS_DIR
    path = d / f"{target_id}.md"
    if not path.exists():
        raise StandardsError(f"no standard for target_id={target_id!r} at {path}")
    return _parse_markdown(path.read_text(encoding="utf-8"), source_path=path)


def list_standards(standards_dir: Path | None = None) -> list[Standard]:
    """Load every *.md under standards_dir. Sorted by target_id for stable output.

    Files named with a leading underscore (e.g. _template.md) are skipped --
    useful for templates the loader shouldn't try to validate.
    """
    d = standards_dir or _STANDARDS_DIR
    out: list[Standard] = []
    if not d.exists():
        return out
    for p in sorted(d.glob("*.md")):
        if p.name.startswith("_"):
            continue
        out.append(_parse_markdown(p.read_text(encoding="utf-8"), source_path=p))
    return out
