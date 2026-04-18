# SPDX-License-Identifier: Apache-2.0
"""PCE Local Hook – Port scanner for local model servers.

Probes known ports to detect running local AI model servers (Ollama,
LM Studio, vLLM, LocalAI, etc.) and returns a list of active endpoints.
"""

import logging
import socket
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import LOCAL_MODEL_PORTS

logger = logging.getLogger("pce.scanner")

# ---------------------------------------------------------------------------
# Probe endpoints per provider
# ---------------------------------------------------------------------------

PROBE_ENDPOINTS: dict[int, list[dict]] = {
    11434: [  # Ollama
        {"path": "/api/tags", "method": "GET", "provider": "ollama"},
        {"path": "/api/version", "method": "GET", "provider": "ollama"},
    ],
    1234: [  # LM Studio
        {"path": "/v1/models", "method": "GET", "provider": "lm-studio"},
    ],
    8000: [  # vLLM / FastChat
        {"path": "/v1/models", "method": "GET", "provider": "vllm"},
        {"path": "/health", "method": "GET", "provider": "vllm"},
    ],
    8080: [  # LocalAI
        {"path": "/v1/models", "method": "GET", "provider": "localai"},
        {"path": "/readyz", "method": "GET", "provider": "localai"},
    ],
    5000: [  # Text Generation WebUI
        {"path": "/v1/models", "method": "GET", "provider": "text-gen-webui"},
        {"path": "/api/v1/model", "method": "GET", "provider": "text-gen-webui"},
    ],
    3000: [  # Jan
        {"path": "/v1/models", "method": "GET", "provider": "jan"},
    ],
}

# Generic fallback probes for unknown ports
GENERIC_PROBES = [
    {"path": "/v1/models", "method": "GET"},
    {"path": "/api/tags", "method": "GET"},
    {"path": "/health", "method": "GET"},
]


@dataclass
class DiscoveredServer:
    """Represents a discovered local model server."""
    host: str
    port: int
    provider: str
    probe_path: str
    models: list[str]


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Quick TCP check if a port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


def probe_server(host: str, port: int, timeout: float = 2.0) -> Optional[DiscoveredServer]:
    """Probe a specific host:port for a local model server.

    Returns a DiscoveredServer if a known AI model server is detected, else None.
    """
    if not is_port_open(host, port):
        return None

    probes = PROBE_ENDPOINTS.get(port, GENERIC_PROBES)

    for probe in probes:
        try:
            url = f"http://{host}:{port}{probe['path']}"
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(probe["method"], url)

            if resp.status_code < 400:
                provider = probe.get("provider", "local-model")
                models = _extract_models(resp)
                logger.info(
                    "Discovered %s on %s:%d via %s (%d models)",
                    provider, host, port, probe["path"], len(models),
                )
                return DiscoveredServer(
                    host=host,
                    port=port,
                    provider=provider,
                    probe_path=probe["path"],
                    models=models,
                )
        except Exception:
            continue

    return None


def scan_known_ports(host: str = "127.0.0.1") -> list[DiscoveredServer]:
    """Scan all known local model ports and return discovered servers."""
    discovered = []
    for port in sorted(LOCAL_MODEL_PORTS):
        server = probe_server(host, port)
        if server:
            discovered.append(server)
    logger.info("Port scan complete: %d servers found", len(discovered))
    return discovered


def _extract_models(resp: httpx.Response) -> list[str]:
    """Best-effort extraction of model names from probe response."""
    try:
        data = resp.json()
    except Exception:
        return []

    models = []

    # OpenAI-compatible: {"data": [{"id": "model-name"}, ...]}
    if isinstance(data, dict) and "data" in data:
        for item in data.get("data", []):
            if isinstance(item, dict) and "id" in item:
                models.append(item["id"])
        if models:
            return models

    # Ollama: {"models": [{"name": "llama3"}, ...]}
    if isinstance(data, dict) and "models" in data:
        for item in data.get("models", []):
            if isinstance(item, dict) and "name" in item:
                models.append(item["name"])
        if models:
            return models

    return models
