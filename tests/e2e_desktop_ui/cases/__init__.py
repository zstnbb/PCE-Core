# SPDX-License-Identifier: Apache-2.0
"""Per-D-case scripts. Each module is a standalone runnable.

Naming: ``<product>_<region>_<window>_<scope>.py``

Currently (2026-05-10 P1 Claude Desktop chat full sweep):

- ``p1_chat_window_a``      — D03 multi-turn + D07 code block + D04
                              cancel mid-stream, in one 5-turn
                              conversation. D03/D07 PASS, D04 known
                              bug (request captured but no message
                              persisted).
- ``p1_chat_window_b_d11``  — D11 long-context (50 turns, >=8K tokens
                              cumulative). PASS — 100/100 messages,
                              1 session, 14378 cumulative tokens.
- ``p1_chat_window_c_d12``  — D12 silent on idle (5 min + 10 s).
                              PASS — 0 chat-relevant writes,
                              +8 raw_captures heartbeat noise only.
- ``p1_chat_window_d_d06``  — D06 attachment (CSV via clipboard
                              CF_HDROP paste). PASS — file_uuid +
                              tool_calls preserved in content_json.
- ``p1_chat_window_e_d10``  — D10 mid-stream proxy kill + restart.
                              PASS — fail-closed semantics, no
                              phantom message, restart healthy.

See ``Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md``
for the empirical evidence + reproduction recipe.
"""
