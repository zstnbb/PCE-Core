# SPDX-License-Identifier: Apache-2.0
"""PCE SDK Capture — LiteLLM proxy bridge (P2).

A fourth capture channel alongside browser extension, mitmproxy and MCP.
Users point their OpenAI / Anthropic / Gemini SDK at
``http://127.0.0.1:9900/v1`` and every completion is persisted to PCE
while passing through to the real provider.

The bridge consists of three pieces:

- :mod:`pce_core.sdk_capture_litellm.config` — generates the
  ``litellm_config.yaml`` that PCE hands to the subprocess.
- :mod:`pce_core.sdk_capture_litellm.callback` — a
  ``litellm.integrations.custom_logger.CustomLogger`` subclass that
  LiteLLM invokes on every completion; it forwards the call to the PCE
  Ingest API as ``source_type='local_hook'`` with ``source_name='sdk-litellm'``.
- :mod:`pce_core.sdk_capture_litellm.bridge` — :class:`LiteLLMBridge`,
  the lifecycle manager that writes the config, spawns ``litellm``
  via :class:`pce_core.supervisor.Supervisor`, and exposes status.

LiteLLM is an **optional** dependency. Every entry point degrades to a
clear "litellm not installed" error instead of crashing the import.
"""

from __future__ import annotations

from .bridge import LiteLLMBridge, LITELLM_AVAILABLE, DEFAULT_LITELLM_PORT
from .callback import PCECallback, pce_callback_handler
from .config import build_litellm_config, write_litellm_config

__all__ = [
    "DEFAULT_LITELLM_PORT",
    "LITELLM_AVAILABLE",
    "LiteLLMBridge",
    "PCECallback",
    "build_litellm_config",
    "pce_callback_handler",
    "write_litellm_config",
]
