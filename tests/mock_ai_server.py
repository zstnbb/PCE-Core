"""A minimal mock AI API server that mimics OpenAI's /v1/chat/completions.

Usage:
    python tests/mock_ai_server.py
    # Listens on http://127.0.0.1:9999
"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockAIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond({"object": "list", "data": [{"id": "model-mock", "object": "model"}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            req = json.loads(body) if body else {}
        except json.JSONDecodeError:
            req = {}

        model = req.get("model", "mock-model")
        reply = {
            "id": "chatcmpl-mock-001",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello from PCE mock server!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        }
        self._respond(reply)

    def _respond(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[mock] {args[0]}")


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "9999"))
    server = HTTPServer(("127.0.0.1", port), MockAIHandler)
    print(f"Mock AI server listening on http://127.0.0.1:{port}")
    server.serve_forever()
