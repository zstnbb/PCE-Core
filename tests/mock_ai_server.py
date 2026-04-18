# SPDX-License-Identifier: Apache-2.0
"""Mock AI API Servers for PCE E2E testing.

Provides three FastAPI apps that faithfully mimic the real API schemas:
  - OpenAI  (/v1/chat/completions, /v1/models)
  - Anthropic (/v1/messages)
  - Ollama  (/api/chat, /api/tags)

Usage:
    # As standalone servers (for manual testing):
    python tests/mock_ai_server.py                    # OpenAI on :19001
    python tests/mock_ai_server.py --provider anthropic --port 19002
    python tests/mock_ai_server.py --provider ollama   --port 19003

    # Programmatic (used by test_e2e_full.py):
    from tests.mock_ai_server import create_openai_app, create_anthropic_app, create_ollama_app
"""

import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ═══════════════════════════════════════════════════════════════════════════
# OpenAI Mock
# ═══════════════════════════════════════════════════════════════════════════

def create_openai_app() -> FastAPI:
    app = FastAPI(title="Mock OpenAI")

    @app.get("/v1/models")
    async def list_models():
        return {
            "object": "list",
            "data": [
                {"id": "gpt-4", "object": "model", "owned_by": "openai"},
                {"id": "gpt-3.5-turbo", "object": "model", "owned_by": "openai"},
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        model = body.get("model", "gpt-4")
        messages = body.get("messages", [])

        # Build a reply that references the user's message
        user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                user_msg = m.get("content", "")

        reply_content = f"Mock OpenAI reply to: {user_msg[:80]}" if user_msg else "Mock OpenAI reply"

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply_content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": max(10, len(user_msg) // 4),
                "completion_tokens": max(5, len(reply_content) // 4),
                "total_tokens": max(15, (len(user_msg) + len(reply_content)) // 4),
            },
        }

    return app


# ═══════════════════════════════════════════════════════════════════════════
# Anthropic Mock
# ═══════════════════════════════════════════════════════════════════════════

def create_anthropic_app() -> FastAPI:
    app = FastAPI(title="Mock Anthropic")

    @app.post("/v1/messages")
    async def create_message(request: Request):
        body = await request.json()
        model = body.get("model", "claude-3-5-sonnet-20241022")
        messages = body.get("messages", [])
        system = body.get("system", "")

        user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    # content blocks format
                    user_msg = " ".join(
                        b.get("text", "") for b in content if b.get("type") == "text"
                    )
                else:
                    user_msg = content

        reply_content = f"Mock Anthropic reply to: {user_msg[:80]}" if user_msg else "Mock Anthropic reply"

        return {
            "id": f"msg_{uuid.uuid4().hex[:20]}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": reply_content}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": max(10, len(user_msg) // 4),
                "output_tokens": max(5, len(reply_content) // 4),
            },
        }

    return app


# ═══════════════════════════════════════════════════════════════════════════
# Ollama Mock
# ═══════════════════════════════════════════════════════════════════════════

def create_ollama_app() -> FastAPI:
    app = FastAPI(title="Mock Ollama")

    @app.get("/api/tags")
    async def list_tags():
        return {
            "models": [
                {"name": "llama3:latest", "model": "llama3:latest", "size": 4_000_000_000},
                {"name": "mistral:latest", "model": "mistral:latest", "size": 4_000_000_000},
            ]
        }

    @app.post("/api/chat")
    async def chat(request: Request):
        body = await request.json()
        model = body.get("model", "llama3")
        messages = body.get("messages", [])

        user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                user_msg = m.get("content", "")

        reply_content = f"Mock Ollama reply to: {user_msg[:80]}" if user_msg else "Mock Ollama reply"

        return {
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": {"role": "assistant", "content": reply_content},
            "done": True,
            "total_duration": 1_500_000_000,
            "load_duration": 500_000_000,
            "prompt_eval_count": max(10, len(user_msg) // 4),
            "eval_count": max(5, len(reply_content) // 4),
        }

    return app


# ═══════════════════════════════════════════════════════════════════════════
# Standalone runner
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="openai", choices=["openai", "anthropic", "ollama"])
    parser.add_argument("--port", type=int, default=19001)
    args = parser.parse_args()

    factories = {
        "openai": create_openai_app,
        "anthropic": create_anthropic_app,
        "ollama": create_ollama_app,
    }

    app = factories[args.provider]()
    print(f"Mock {args.provider} server on http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
