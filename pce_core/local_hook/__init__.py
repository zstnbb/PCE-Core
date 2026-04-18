# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Local Model Hook.

A lightweight reverse-proxy that sits in front of local AI model servers
(Ollama, LM Studio, vLLM, LocalAI, etc.) and captures all requests/responses
to the PCE Ingest API while transparently forwarding traffic.

Usage:
    python -m pce_core.local_hook                  # auto-detect Ollama on :11434
    python -m pce_core.local_hook --target 1234    # proxy LM Studio on :1234
    python -m pce_core.local_hook --target localhost:8000 --listen 11435
"""
