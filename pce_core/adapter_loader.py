# SPDX-License-Identifier: Apache-2.0
"""pce_core.adapter_loader — load + validate YAML site adapter manifests.

P5.C.4.1 deliverable per HANDOFF-META-PIPELINE-KICKOFF §4.P5.C.4. Read
half of the YAML-as-source-of-truth refactor:

- Reads ``pce_core/adapters/<site>.yaml``
- Validates required fields + selector lists + timeout types
- Returns an ``AdapterConfig`` dataclass + an ``apply_to_class`` helper
  that mirrors the values onto a ``BaseProbeSiteAdapter`` subclass

P5.C.4.2 will replace the class-attribute blocks in
``tests/e2e_probe/sites/{chatgpt,claude,gemini}.py`` with a single
``apply_to_class(cls, "<site>")`` call. P5.C.4.3 will reuse the same
shape on the conductor side so ``propose_patch`` emits YAML diffs.

Dependency-light: stdlib + pyyaml (already in PCE Core deps). No
jsonschema; validation lives here in plain Python so error messages
can name specific YAML keys, not schema fragments.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

logger = logging.getLogger("pce.adapter_loader")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LATEST_SCHEMA_VERSION: int = 1
DEFAULT_ADAPTERS_DIR: Path = Path(__file__).parent / "adapters"

#: All supported selector groups. Adding one requires extending the
#: ``SELECTOR_GROUP_TO_ATTR`` mapping below. Unknown groups still load
#: but ``apply_to_class`` projects them onto ``<group>_selectors``
#: with a warning (forward-compat for new affordances).
SUPPORTED_SELECTOR_GROUPS: tuple[str, ...] = (
    # Core composer + response affordances
    "input", "send_button", "stop_button", "response_container",
    "login_wall", "edit_button", "edit_input", "regenerate_button",
    "more_actions_button", "blocking_state",
    # File / image uploads
    "file_input", "image_input",
    # T06 model switcher / T14 web search
    "model_switcher", "web_search_button",
    # T12 / T13 / T15 advanced-tool affordances
    "tool_picker", "code_interpreter_button",
    "canvas_button", "image_gen_button",
    "canvas_indicator",
    # T09 branch flip
    "branch_root", "branch_user_root", "branch_assistant_root",
    "branch_prev", "branch_next",
    # T19 error banner
    "error_banner",
)

SUPPORTED_TIMEOUT_KEYS: tuple[str, ...] = (
    "page_load", "input_appear", "response", "submit_verify",
    "response_stability", "type_settle", "post_send_settle",
)

#: Regex flag string -> re module flag.
_FLAG_MAP: dict[str, int] = {
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
}

#: Mapping from YAML selector group key -> BaseProbeSiteAdapter class
#: attribute name. Used by apply_to_class to project YAML onto Python.
SELECTOR_GROUP_TO_ATTR: dict[str, str] = {
    "input": "input_selectors",
    "send_button": "send_button_selectors",
    "stop_button": "stop_button_selectors",
    "response_container": "response_container_selectors",
    "login_wall": "login_wall_selectors",
    "edit_button": "edit_button_selectors",
    "edit_input": "edit_input_selectors",
    "regenerate_button": "regenerate_button_selectors",
    "more_actions_button": "more_actions_button_selectors",
    "file_input": "file_input_selectors",
    "image_input": "image_input_selectors",
    "branch_root": "branch_root_selectors",
    "branch_user_root": "branch_user_root_selectors",
    "branch_assistant_root": "branch_assistant_root_selectors",
    "blocking_state": "blocking_state_selectors",
    # P5.C.4.2 additions — full coverage of chatgpt.py / claude.py / gemini.py.
    "model_switcher": "model_switcher_selectors",
    "tool_picker": "tool_picker_selectors",
    "code_interpreter_button": "code_interpreter_button_selectors",
    "canvas_button": "canvas_button_selectors",
    "image_gen_button": "image_gen_button_selectors",
    "web_search_button": "web_search_button_selectors",
    "canvas_indicator": "canvas_indicator_selectors",
    "branch_prev": "branch_prev_selectors",
    "branch_next": "branch_next_selectors",
    "error_banner": "error_banner_selectors",
    # P5.C.5.2 additions — covering Grok / Google AI Studio / other sites.
    "regenerate_root": "regenerate_root_selectors",
    "branch_from_here": "branch_from_here_selectors",
}

#: Mapping from YAML labels key -> class attribute name. Same convention
#: as selectors: ``<key>_labels``. Used for menu-item text matching
#: (regenerate menu, reasoning model picker, etc.).
LABEL_GROUP_TO_ATTR: dict[str, str] = {
    "regenerate_menu": "regenerate_menu_labels",
    "reasoning_model": "reasoning_model_labels",
    "code_interpreter_menu": "code_interpreter_menu_labels",
    "canvas_menu": "canvas_menu_labels",
    "image_gen_menu": "image_gen_menu_labels",
    # P5.C.5.2 additions.
    "preferred_model": "preferred_model_labels",
    "branch_from_here_menu": "branch_from_here_menu_labels",
    # ``blocking_state`` is an exception to the ``<group>_labels``
    # convention: the BaseProbeSiteAdapter class attribute is
    # ``blocking_state_keywords`` (matches against page text content,
    # not selector matching), so we map it explicitly here. Grok uses
    # this surface to detect rate-limit / quota banners.
    "blocking_state": "blocking_state_keywords",
}

#: Mapping from YAML prompts key -> class attribute name. Convention
#: ``<key>_prompt``. Holds the long multi-line trigger prompts for
#: T12 / T13 / T15 / T19.
PROMPT_KEY_TO_ATTR: dict[str, str] = {
    "canvas_trigger": "canvas_trigger_prompt",
    "code_interp_trigger": "code_interp_trigger_prompt",
    "image_gen_trigger": "image_gen_trigger_prompt",
    "error_trigger": "error_trigger_prompt",
}

#: Mapping from YAML flags key -> class attribute name. Convention is
#: name-as-is (no suffix). Holds the boolean / string overrides like
#: ``regenerate_prefer_dom_click`` and ``branch_creation_mode``.
FLAG_KEY_TO_ATTR: dict[str, str] = {
    "regenerate_prefer_dom_click": "regenerate_prefer_dom_click",
    "branch_creation_mode": "branch_creation_mode",
    "image_gen_invocation": "image_gen_invocation",
    # P5.C.5.2 additions.
    "branch_surface_supported": "branch_surface_supported",
    "inter_cell_pacing_s": "inter_cell_pacing_s",
}

TIMEOUT_KEY_TO_ATTR: dict[str, str] = {
    "page_load": "page_load_timeout_ms",
    "input_appear": "input_appear_timeout_ms",
    "response": "response_timeout_ms",
    "submit_verify": "submit_verify_timeout_ms",
    "response_stability": "response_stability_ms",
    "type_settle": "type_settle_ms",
    "post_send_settle": "post_send_settle_ms",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class AdapterValidationError(ValueError):
    """Raised when a YAML manifest violates the schema."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class AdapterConfig:
    """Resolved adapter manifest. JSON-serialisable shape.

    Group containers are stored as single dicts so unknown / future
    entries round-trip without code changes; the typed accessors
    (``selectors_for`` / ``labels_for`` / ``prompt_for`` / ``flag_for``)
    are the safe access path.
    """

    name: str
    provider: str
    url: str
    schema_version: int
    display_name: str
    selectors: dict[str, list[str]]
    timeouts_ms: dict[str, int]
    session_url_pattern: Optional[re.Pattern[str]]
    settings_url: Optional[str]
    temporary_chat_url: Optional[str]
    tier: Optional[str]
    plane: list[str]
    regenerate_menu_labels: list[str]
    canary_endpoint: Optional[str]
    # P5.C.4.2 additions.
    labels: dict[str, list[str]] = field(default_factory=dict)
    prompts: dict[str, Optional[str]] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    source_path: Optional[Path] = None

    def selectors_for(self, group: str) -> list[str]:
        """Return selector list for ``group`` (empty when not declared)."""
        return list(self.selectors.get(group, []))

    def labels_for(self, group: str) -> list[str]:
        """Return label list for ``group`` (empty when not declared)."""
        return list(self.labels.get(group, []))

    def prompt_for(self, key: str, *, default: Optional[str] = None) -> Optional[str]:
        """Return prompt string for ``key`` (default when not declared)."""
        return self.prompts.get(key, default)

    def flag_for(self, key: str, *, default: Any = None) -> Any:
        """Return flag value for ``key`` (default when not declared)."""
        return self.flags.get(key, default)

    def timeout_ms(self, key: str, *, default: Optional[int] = None) -> Optional[int]:
        return self.timeouts_ms.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "url": self.url,
            "schema_version": self.schema_version,
            "display_name": self.display_name,
            "selectors": {k: list(v) for k, v in self.selectors.items()},
            "timeouts_ms": dict(self.timeouts_ms),
            "session_url_pattern": (
                self.session_url_pattern.pattern if self.session_url_pattern else None
            ),
            "settings_url": self.settings_url,
            "temporary_chat_url": self.temporary_chat_url,
            "tier": self.tier,
            "plane": list(self.plane),
            "regenerate_menu_labels": list(self.regenerate_menu_labels),
            "canary_endpoint": self.canary_endpoint,
            "labels": {k: list(v) for k, v in self.labels.items()},
            "prompts": dict(self.prompts),
            "flags": dict(self.flags),
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_adapter(name: str, *, adapters_dir: Optional[Path] = None) -> AdapterConfig:
    """Load + validate one adapter manifest by name.

    Raises:
        FileNotFoundError: when ``<name>.yaml`` is missing.
        AdapterValidationError: on schema violations.
    """
    base = adapters_dir or DEFAULT_ADAPTERS_DIR
    path = base / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"adapter manifest not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return _from_yaml_dict(data, source_path=path)


def list_adapter_names(*, adapters_dir: Optional[Path] = None) -> list[str]:
    """Return sorted list of every ``<name>.yaml`` stem in the directory."""
    base = adapters_dir or DEFAULT_ADAPTERS_DIR
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))


def load_all_adapters(*, adapters_dir: Optional[Path] = None) -> list[AdapterConfig]:
    """Load every manifest. Bad ones are logged + skipped."""
    out: list[AdapterConfig] = []
    for name in list_adapter_names(adapters_dir=adapters_dir):
        try:
            out.append(load_adapter(name, adapters_dir=adapters_dir))
        except (AdapterValidationError, FileNotFoundError) as exc:
            logger.warning("adapter %r failed to load: %r — skipped", name, exc)
    return out


# ---------------------------------------------------------------------------
# apply_to_class — backward-compat bridge to BaseProbeSiteAdapter
# ---------------------------------------------------------------------------

def apply_to_class(cls: type, config: AdapterConfig) -> type:
    """Mirror an ``AdapterConfig`` onto a ``BaseProbeSiteAdapter`` subclass.

    P5.C.4.2 will rewrite the 3 site adapters to use this in place of
    explicit class attributes. The function:

      1. Sets ``cls.name`` / ``cls.provider`` / ``cls.url``
      2. For each selector group, sets the corresponding
         ``<group>_selectors`` tuple on the class (tuple, not list,
         to match the existing ClassVar Sequence[str] type)
      3. For each timeout, sets the ``<key>_<suffix>`` int attribute
      4. Compiles + sets ``session_url_pattern`` if declared
      5. Sets ``settings_url`` / ``temporary_chat_url`` /
         ``regenerate_menu_labels`` if declared

    Returns the class (so this can be used as a decorator if desired).

    Unknown selector groups produce a warning + are still applied to
    the class with the canonical ``<group>_selectors`` attribute name.
    """
    cls.name = config.name  # type: ignore[attr-defined]
    cls.provider = config.provider  # type: ignore[attr-defined]
    cls.url = config.url  # type: ignore[attr-defined]

    for group, selectors in config.selectors.items():
        attr = SELECTOR_GROUP_TO_ATTR.get(group)
        if attr is None:
            attr = f"{group}_selectors"
            logger.warning(
                "adapter %r declares unknown selector group %r → applied as %r",
                config.name, group, attr,
            )
        setattr(cls, attr, tuple(selectors))

    for key, ms in config.timeouts_ms.items():
        attr = TIMEOUT_KEY_TO_ATTR.get(key)
        if attr is None:
            attr = f"{key}_ms"
            logger.warning(
                "adapter %r declares unknown timeout %r → applied as %r",
                config.name, key, attr,
            )
        setattr(cls, attr, int(ms))

    if config.session_url_pattern is not None:
        cls.session_url_pattern = config.session_url_pattern  # type: ignore[attr-defined]
    if config.settings_url is not None:
        cls.settings_url = config.settings_url  # type: ignore[attr-defined]
    if config.temporary_chat_url is not None:
        cls.temporary_chat_url = config.temporary_chat_url  # type: ignore[attr-defined]
    if config.regenerate_menu_labels:
        cls.regenerate_menu_labels = tuple(config.regenerate_menu_labels)  # type: ignore[attr-defined]

    # P5.C.4.2: labels / prompts / flags.
    for group, items in config.labels.items():
        attr = LABEL_GROUP_TO_ATTR.get(group, f"{group}_labels")
        setattr(cls, attr, tuple(items))

    for key, value in config.prompts.items():
        attr = PROMPT_KEY_TO_ATTR.get(key, f"{key}_prompt")
        setattr(cls, attr, value)

    for key, value in config.flags.items():
        attr = FLAG_KEY_TO_ATTR.get(key, key)
        setattr(cls, attr, value)

    return cls


# ---------------------------------------------------------------------------
# Validation + parsing internals
# ---------------------------------------------------------------------------

def _from_yaml_dict(data: dict[str, Any], *, source_path: Optional[Path] = None) -> AdapterConfig:
    """Validate + normalise a parsed YAML mapping into an ``AdapterConfig``."""
    _require_keys(data, ("schema_version", "name", "provider", "url"), source_path)

    schema_version = int(data["schema_version"])
    if schema_version > LATEST_SCHEMA_VERSION:
        raise AdapterValidationError(
            f"manifest at {source_path} declares schema_version={schema_version} "
            f"but loader only understands up to v{LATEST_SCHEMA_VERSION}"
        )

    selectors = _validate_selectors(data.get("selectors") or {}, source_path)
    timeouts = _validate_timeouts(data.get("timeouts_ms") or {}, source_path)
    session_url_pattern = _validate_session_url_pattern(
        data.get("session_url_pattern"),
        data.get("session_url_pattern_flags") or [],
        source_path,
    )
    labels = _validate_label_groups(data.get("labels") or {}, source_path)
    prompts = _validate_prompts(data.get("prompts") or {}, source_path)
    flags = _validate_flags(data.get("flags") or {}, source_path)

    plane = list(data.get("plane") or [])
    # ``regenerate_menu_labels`` is kept as a top-level field for
    # backward compat with P5.C.4.1 YAML files. New code should put it
    # under ``labels.regenerate_menu`` instead. If both are declared,
    # the labels.* form wins.
    regen_labels_top = [str(x) for x in (data.get("regenerate_menu_labels") or [])]
    regen_labels_nested = labels.get("regenerate_menu") or []
    regen_labels = list(regen_labels_nested) if regen_labels_nested else regen_labels_top
    if regen_labels and "regenerate_menu" not in labels:
        # Mirror top-level into labels dict so consumers have a single source.
        labels = {**labels, "regenerate_menu": list(regen_labels)}

    return AdapterConfig(
        name=str(data["name"]),
        provider=str(data["provider"]),
        url=str(data["url"]),
        schema_version=schema_version,
        display_name=str(data.get("display_name") or data["name"]),
        selectors=selectors,
        timeouts_ms=timeouts,
        session_url_pattern=session_url_pattern,
        settings_url=_optional_str(data.get("settings_url")),
        temporary_chat_url=_optional_str(data.get("temporary_chat_url")),
        tier=_optional_str(data.get("tier")),
        plane=[str(p) for p in plane],
        regenerate_menu_labels=regen_labels,
        canary_endpoint=_optional_str(data.get("canary_endpoint")),
        labels=labels,
        prompts=prompts,
        flags=flags,
        source_path=source_path,
    )


def _require_keys(data: dict, keys: Sequence[str], source_path: Optional[Path]) -> None:
    missing = [k for k in keys if k not in data]
    if missing:
        raise AdapterValidationError(
            f"manifest at {source_path} missing required keys: {missing}"
        )


def _validate_selectors(raw: Any, source_path: Optional[Path]) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        raise AdapterValidationError(
            f"manifest at {source_path}: 'selectors' must be a mapping"
        )
    out: dict[str, list[str]] = {}
    for group, selectors in raw.items():
        if not isinstance(group, str):
            raise AdapterValidationError(
                f"manifest at {source_path}: selector group key must be a string, got {group!r}"
            )
        if not isinstance(selectors, list):
            raise AdapterValidationError(
                f"manifest at {source_path}: selectors.{group} must be a list, got {type(selectors).__name__}"
            )
        validated = []
        for i, sel in enumerate(selectors):
            if not isinstance(sel, str) or not sel.strip():
                raise AdapterValidationError(
                    f"manifest at {source_path}: selectors.{group}[{i}] "
                    f"must be a non-empty string, got {sel!r}"
                )
            validated.append(sel)
        out[group] = validated
    return out


def _validate_timeouts(raw: Any, source_path: Optional[Path]) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise AdapterValidationError(
            f"manifest at {source_path}: 'timeouts_ms' must be a mapping"
        )
    out: dict[str, int] = {}
    for key, ms in raw.items():
        if not isinstance(ms, int) or isinstance(ms, bool) or ms <= 0:
            raise AdapterValidationError(
                f"manifest at {source_path}: timeouts_ms.{key} must be a positive int, got {ms!r}"
            )
        out[str(key)] = int(ms)
    return out


def _validate_session_url_pattern(
    pattern: Any, flags: Any, source_path: Optional[Path],
) -> Optional[re.Pattern[str]]:
    if pattern is None:
        return None
    if not isinstance(pattern, str):
        raise AdapterValidationError(
            f"manifest at {source_path}: session_url_pattern must be a string, got {type(pattern).__name__}"
        )
    flag_int = 0
    if not isinstance(flags, list):
        raise AdapterValidationError(
            f"manifest at {source_path}: session_url_pattern_flags must be a list of names"
        )
    for flag in flags:
        if not isinstance(flag, str):
            raise AdapterValidationError(
                f"manifest at {source_path}: flag entries must be strings, got {flag!r}"
            )
        upper = flag.upper()
        if upper not in _FLAG_MAP:
            raise AdapterValidationError(
                f"manifest at {source_path}: unknown regex flag {flag!r}; "
                f"supported: {sorted(_FLAG_MAP)}"
            )
        flag_int |= _FLAG_MAP[upper]
    try:
        return re.compile(pattern, flag_int)
    except re.error as exc:
        raise AdapterValidationError(
            f"manifest at {source_path}: invalid regex {pattern!r}: {exc}"
        ) from exc


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AdapterValidationError(f"expected str or null, got {type(value).__name__}")
    return value


def _validate_label_groups(raw: Any, source_path: Optional[Path]) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        raise AdapterValidationError(
            f"manifest at {source_path}: 'labels' must be a mapping"
        )
    out: dict[str, list[str]] = {}
    for group, items in raw.items():
        if not isinstance(items, list):
            raise AdapterValidationError(
                f"manifest at {source_path}: labels.{group} must be a list, "
                f"got {type(items).__name__}"
            )
        validated: list[str] = []
        for i, item in enumerate(items):
            if not isinstance(item, str) or not item.strip():
                raise AdapterValidationError(
                    f"manifest at {source_path}: labels.{group}[{i}] "
                    f"must be a non-empty string, got {item!r}"
                )
            validated.append(item)
        out[str(group)] = validated
    return out


def _validate_prompts(raw: Any, source_path: Optional[Path]) -> dict[str, Optional[str]]:
    if not isinstance(raw, dict):
        raise AdapterValidationError(
            f"manifest at {source_path}: 'prompts' must be a mapping"
        )
    out: dict[str, Optional[str]] = {}
    for key, value in raw.items():
        if value is not None and not isinstance(value, str):
            raise AdapterValidationError(
                f"manifest at {source_path}: prompts.{key} must be a string or null, "
                f"got {type(value).__name__}"
            )
        out[str(key)] = value
    return out


def _validate_flags(raw: Any, source_path: Optional[Path]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AdapterValidationError(
            f"manifest at {source_path}: 'flags' must be a mapping"
        )
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if value is not None and not isinstance(value, (bool, str, int, float)):
            raise AdapterValidationError(
                f"manifest at {source_path}: flags.{key} must be a primitive scalar or null, "
                f"got {type(value).__name__}"
            )
        out[str(key)] = value
    return out
