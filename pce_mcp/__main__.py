"""Run PCE MCP server.

Usage:
  python -m pce_mcp              # stdio transport (for Claude Desktop, Cursor, etc.)
  python -m pce_mcp --sse        # SSE transport on port 9801
"""

import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from .server import mcp

if __name__ == "__main__":
    if "--sse" in sys.argv:
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
