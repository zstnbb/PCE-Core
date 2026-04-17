"""LiteLLM config-file generator.

PCE owns the config: we pick the port, declare our custom callback, and
enable pass-through mode so *any* SDK can point at us without pre-declaring
a model alias.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional


# PCE's callback class path for litellm's ``callbacks:`` directive.
PCE_CALLBACK_PATH = "pce_core.sdk_capture_litellm.callback.pce_callback_handler"


def build_litellm_config(
    pce_ingest_url: str = "http://127.0.0.1:9800/api/v1/captures",
    port: int = 9900,
    models: Optional[List[dict]] = None,
    *,
    master_key: Optional[str] = None,
) -> str:
    """Return a ``litellm_config.yaml`` as a string.

    We ship a minimal but complete config:

    - ``general_settings.master_key`` left ``None`` so local SDKs need no key.
    - ``litellm_settings.success_callback`` / ``failure_callback`` point at
      PCE's callback handler.
    - ``environment_variables.PCE_INGEST_URL`` is stamped in so the callback
      knows where to POST without relying on the parent process env.
    - ``model_list`` is empty by default — LiteLLM's pass-through router
      forwards ``openai/*`` / ``anthropic/*`` / ``gemini/*`` prefixes to the
      real upstreams as long as the user's SDK sends the right API key.
    """
    import json

    model_list = models if models is not None else []
    # Use JSON-style literals so we don't pull in PyYAML as a hard dep.
    # LiteLLM accepts either YAML or JSON (YAML is a superset).
    cfg = {
        "model_list": model_list,
        "litellm_settings": {
            "success_callback": [PCE_CALLBACK_PATH],
            "failure_callback": [PCE_CALLBACK_PATH],
            "set_verbose": False,
        },
        "general_settings": {
            "master_key": master_key,
            "pass_through_endpoints": [
                {"path": "/openai", "target": "https://api.openai.com"},
                {"path": "/anthropic", "target": "https://api.anthropic.com"},
                {"path": "/gemini", "target": "https://generativelanguage.googleapis.com"},
            ],
        },
        "environment_variables": {
            "PCE_INGEST_URL": pce_ingest_url,
            "PCE_LITELLM_PORT": str(port),
        },
    }
    return json.dumps(cfg, indent=2, ensure_ascii=False)


def write_litellm_config(
    path: Path,
    pce_ingest_url: str = "http://127.0.0.1:9800/api/v1/captures",
    port: int = 9900,
    models: Optional[List[dict]] = None,
    *,
    master_key: Optional[str] = None,
) -> Path:
    """Write the config to *path*. Parents are created if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_litellm_config(
            pce_ingest_url=pce_ingest_url, port=port,
            models=models, master_key=master_key,
        ),
        encoding="utf-8",
    )
    return path
