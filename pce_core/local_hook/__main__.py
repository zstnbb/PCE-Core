# SPDX-License-Identifier: Apache-2.0
"""Run the PCE Local Model Hook.

Usage:
    python -m pce_core.local_hook                          # Ollama :11434 → hook :11435
    python -m pce_core.local_hook --target 1234            # LM Studio :1234 → hook :1235
    python -m pce_core.local_hook --target localhost:8000 --listen 8001
"""

import argparse
import logging
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from .hook import create_hook_app, DEFAULT_TARGET_HOST, DEFAULT_TARGET_PORT, DEFAULT_LISTEN_PORT


def main():
    parser = argparse.ArgumentParser(description="PCE Local Model Hook")
    parser.add_argument(
        "--target",
        default=f"{DEFAULT_TARGET_HOST}:{DEFAULT_TARGET_PORT}",
        help="Target model server as host:port or just port (default: 127.0.0.1:11434)",
    )
    parser.add_argument(
        "--listen",
        type=int,
        default=0,
        help="Port to listen on (default: target_port + 1)",
    )
    args = parser.parse_args()

    # Parse target
    target = args.target
    if ":" in target:
        parts = target.split(":")
        target_host = parts[0]
        target_port = int(parts[1])
    else:
        target_host = DEFAULT_TARGET_HOST
        target_port = int(target)

    listen_port = args.listen if args.listen else target_port + 1

    app = create_hook_app(target_host, target_port)

    print(f"\n  PCE Local Hook")
    print(f"  Hook listens on:  http://127.0.0.1:{listen_port}")
    print(f"  Forwards to:      http://{target_host}:{target_port}")
    print(f"  Point your apps at port {listen_port} instead of {target_port}\n")

    uvicorn.run(app, host="127.0.0.1", port=listen_port, log_level="info")


if __name__ == "__main__":
    main()
