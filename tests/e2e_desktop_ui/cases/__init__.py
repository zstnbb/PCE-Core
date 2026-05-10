# SPDX-License-Identifier: Apache-2.0
"""Per-D-case scripts. Each module is a standalone runnable.

Naming: ``<product>_<region>_<window>_<scope>.py``

The framework now spans the **full Claude Desktop chat D-case matrix**
(D00–D12 first-pass + D13–D22 web-parity extension):

First-pass sweep (2026-05-10 commit ``28eadd7``):

- ``p1_chat_window_a``      — D03 multi-turn + D07 code block + D04
                              cancel mid-stream. D03/D07 PASS, D04
                              KNOWN BUG (request captured but no
                              message persisted).
- ``p1_chat_window_b_d11``  — D11 long-context (50 turns, >=8K tokens).
                              PASS — 100/100 messages, 1 session,
                              14378 cumulative tokens.
- ``p1_chat_window_c_d12``  — D12 silent on idle (5 min + 10 s).
                              PASS — 0 chat-relevant writes.
- ``p1_chat_window_d_d06``  — D06 attachment (CSV via CF_HDROP).
                              PASS — file_uuid + tool_calls preserved.
- ``p1_chat_window_e_d10``  — D10 mid-stream proxy kill + restart.
                              PASS — fail-closed semantics.

Web-parity extension (2026-05-10 same-day, this commit):

- ``p1_chat_window_f_d13``         — D13 Extended Thinking
                                     (``thinking_delta`` SSE events +
                                     clean assistant content_text).
- ``p1_chat_window_g_d14_d15_d16`` — D14 edit / D15 regenerate /
                                     D16 branch flip (one shared 4-turn
                                     conversation).
- ``p1_chat_window_h_d17``         — D17 image / vision (PNG via
                                     CF_HDROP, OCR'd token round-trip).
- ``p1_chat_window_i_d18``         — D18 PDF document (CF_HDROP +
                                     summarisation).
- ``p1_chat_window_j_d19``         — D19 project scope (gated on
                                     ``CLAUDE_PROJECT_NAME`` env var).
- ``p1_chat_window_k_d20``         — D20 artifact text/markdown
                                     (``input_json_delta`` reconcile).
- ``p1_chat_window_l_d21``         — D21 artifact React component
                                     (same delta path, JSX/TSX body).
- ``p1_chat_window_m_d22``         — D22 Writing Style
                                     (``personalized_styles`` capture).

Each case writes ``_baseline_ts.txt`` at start so its companion
inspector or its own inline post-run checks can filter by it. Cases
emit a per-D verdict (PASS / PARTIAL / SKIP / FAIL) on stdout. Combo
windows emit one verdict per D (e.g. window_g emits D14 + D15 + D16).

See ``Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md``
(first pass) and the second-pass handoff filed alongside this commit
for empirical evidence + reproduction recipe.
"""
