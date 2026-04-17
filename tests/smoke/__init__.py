"""PCE smoke tests.

Each module in this package exercises one end-to-end capture path
(migrations, health API, proxy addon, MCP tool, browser extension
ingest) against an isolated temp database.

Tests are designed to run in CI without requiring:
- A running mitmdump / Chrome / MCP client
- Network access
- Persistent user data

They are the canonical gate for P0 (2026-04-17) and above. New
capture surfaces must add a smoke test here before their phase is
considered complete.
"""
