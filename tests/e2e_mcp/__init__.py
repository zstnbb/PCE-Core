# SPDX-License-Identifier: Apache-2.0
"""End-to-end stdio MCP protocol tests for pce_mcp.

These tests spawn `python -m pce_mcp` as a subprocess and exercise
the real JSON-RPC 2.0 wire protocol, verifying what an actual MCP
host (Claude Desktop, Cursor, Windsurf, Claude Code, etc.) sees.

Where unit tests in tests/test_mcp.py validate the tool functions
in-process via direct Python imports, this file validates the
stdio protocol surface end-to-end.
"""
