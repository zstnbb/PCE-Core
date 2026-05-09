# Changelog

All notable changes to PCE (core + browser extension) are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0-alpha.2] - 2026-05-09 — P5.B.1: `pce_mcp_proxy` (UCS L3f, posture B)

The MCP middleware proxy ships, completing the M-plane capture story
opened in P5.B.0. Posture A (`pce_mcp/`, agent-cooperative ledger) and
posture B (`pce_mcp_proxy/`, transparent wire-tap) are now both
production-grade and complementary. UCS layer L3f is formally amended
into the architecture by ADR-015.

### Added

- `pce_mcp_proxy/` — new OSS package (Apache-2.0 per ADR-013/015):
  threading-based bidirectional stdio relay that wraps any upstream
  MCP server, transparently forwarding host ↔ upstream JSON-RPC 2.0
  frames while side-channelling each frame into PCE. 6 files,
  ~1100 LoC; cross-platform (Windows / macOS / Linux).
  - `Relay` — wire-loss-free duplex forwarder with decoupled
    observation thread so JSON parsing latency cannot stall protocol
    bytes.
  - `JsonRpcObserver` — threadsafe frame classifier (request /
    notification / response / response_error / server_initiated)
    with pending-id pairing and best-effort SQLite write.
  - `RelayConfig` + argv split-on-`--` parser; `--upstream-name /
    --data-dir / --print-stats / --quiet / --log-file` flags.
- `pce_core/migrations/0009_mcp_proxy_source.py` — registers the
  `mcp-proxy-default` source row (idempotent INSERT OR IGNORE).
  Bumps `EXPECTED_SCHEMA_VERSION` 8 → 9.
- `pce_core/db.py::SOURCE_MCP_PROXY` constant (`mcp-proxy-default`)
  and matching `_DEFAULT_SOURCES` entry. Distinct from `SOURCE_MCP`
  so the dashboard can tell wire-tapped traffic from agent-reported
  traffic at a glance.
- `pce_core/normalizer/mcp_jsonrpc.py` — Tier 1 normaliser for
  `tools/call` / `resources/read` / `prompts/get` JSON-RPC pairs.
  Produces OpenAI-style `role=assistant` (with `tool_calls`) +
  `role=tool` (with `tool_call_id`) message pairs, namespaced as
  `<upstream>.<tool_name>` for cross-upstream uniqueness, dedup-
  resistant via `tool_call_id` suffix in `content_text`.
- `tests/e2e_mcp/_mock_upstream.py` — scriptable mini MCP server
  driven by `PCE_MOCK_RESPONSES` env var; supports `@@delay_ms`,
  `@@drop`, `@@close`, `@@exit_code`, server-initiated frames.
- `tests/e2e_mcp/test_pce_mcp_proxy_stdio.py` — 11 end-to-end cases
  (R01 – R11) covering handshake forwarding, capture pair, Tier 1
  normalisation, tool-level + JSON-RPC-level error paths, multi-call
  session collapsing, byte passthrough, upstream exit code
  propagation, and missing-upstream error handling. All GREEN.
- `Docs/install/PCE_MCP_PROXY_INSTALL.md` — 8-host install guide
  (Claude Desktop / Cursor / Windsurf / Claude Code / Codex CLI /
  Gemini CLI / Cascade-Windsurf / generic) with concrete diff-from-
  vanilla configs and a §9 section on running posture A and B
  side-by-side.
- `pce_mcp_proxy/README.md` — package boundary contract (MUST /
  MUST NOT) + architecture diagram + cross-references.
- `Docs/docs/engineering/adr/ADR-015-ucs-l3f-mcp-middleware.md` —
  formal UCS amendment introducing L3f as a first-class layer,
  closing the architectural debt P5.B.1 carried.

### Changed

- `pce_core/normalizer/registry.py::_auto_register` now registers
  `MCPJsonRpcNormalizer` between Anthropic and the conversation
  catch-all. Existing OpenAI / Anthropic / conversation behaviour is
  unchanged.
- `pce_mcp/README.md` — §M 面姿态表 status for posture B flipped from
  ⏳ (P5.B.1 implement) to ✅ (P5.B.1 landed 2026-05-09); added a
  paragraph on running A + B together.
- `Docs/docs/PROJECT.md` — ADR list + reading order updated to mark
  ADR-015 as landed; architecture v0.3 description is now present
  tense rather than aspirational.
- `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` and
  `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` updated to reflect the
  P5.B.1 milestone.

### Test counts

- `tests/e2e_mcp/` — **22/22 GREEN** (11 posture-A stdio + 11
  posture-B relay).
- `tests/test_mcp.py` — **11/11 GREEN** (in-process posture-A).
- Combined: **33/33 GREEN**, zero regressions on the posture-A
  surface from P5.B.0.

### Known limitations (deferred)

- v1 normaliser only handles `tools/call` / `resources/read` /
  `prompts/get`. `initialize` / `tools/list` / `ping` /
  `sampling/createMessage` stay in Tier 0 only — see ADR-015 §7 for
  the rationale and re-open criteria.
- Session boundaries use a day-bucketed key (`mcp-proxy:<upstream>:
  <YYYY-MM-DD>`); P5.B.2 should refine to lifecycle-bracketed
  sessions tied to the proxy process boundary.
- Observation queue is unbounded; tighten with a high-water mark +
  drop policy when production data shows the need.

## [1.1.0-alpha.1] - 2026-05-08 — P5.B.0: `pce_mcp` formalisation

Posture A (`pce_mcp/` as MCP server) is named, documented, and
covered by real wire-protocol e2e tests. The OSS classification
debate is closed by ADR-013, the P5.B scope re-ordering is recorded
by ADR-012, and the Type 5/6/7 deferral is closed by ADR-014. Browser
subsystem hard-frozen; all forward motion now lives in P5.B.

### Added

- `pce_mcp/README.md` — explicit posture A vs B vs C boundary.
- `Docs/install/PCE_MCP_INSTALL.md` — 8-host install guide for
  posture A.
- `tests/e2e_mcp/` — subprocess + JSON-RPC stdio harness with
  `MCPStdioClient`; 11 e2e cases (E01 – E11) all GREEN.
- ADRs 012 / 013 / 014.

### Fixed

- **FastMCP `pre_parse_json` interop bug discovered during e2e**:
  `pce_capture` body fields were `str | None`, which made FastMCP
  silently `json.loads` JSON-encoded conversation/request/response
  bodies into dicts before pydantic validation. Strict MCP hosts
  (notably Claude Desktop) hit this on every call. Fix: declare body
  fields as plain `str = ""` so the auto-parse path skips them.
  Documented in `pce_mcp/server.py` docstring + ADR-013 footnote.

## [1.0.1] - 2026-04-22 — first public release (v1.0.0 was rejected in review)

v1.0.0 was submitted to the Chrome Web Store on 2026-04-21 and rejected on
2026-04-22 for a keyword-spam violation in the description (transfer ID
`FZSL`, "Yellow Argon"). v1.0.1 is the first version that actually reaches
Chrome Web Store users.

### Added

- `Docs/stability/REQUIREBOTHROLES-AUDIT.md` — cross-site audit of the
  partial-capture defense gate after the Copilot MCP6 fix. Documents which
  sites already have it (Copilot, Zhipu, Poe, Grok, M365), which have
  equivalent native defenses (ChatGPT), and which remain v1.0.2 candidates
  without live-user evidence (Claude, Gemini, DeepSeek).
- `CHANGELOG.md` (this file).
- README.md `### Browser extension scope` subsection listing exactly the
  25 host patterns in the extension manifest, giving Chrome Web Store
  reviewers (and users) a source of truth that round-trips to
  `wxt.config.ts COVERED_SITES`.

### Fixed — browser extension behavior

- **Copilot empty-reply capture (P5B-PLAN P0 exit criterion, MCP6).**
  Copilot's React UI briefly shows the user turn before the assistant
  turn's `.ac-textBlock` populates. Two compounding issues let partial
  captures through:
  - `extractText` returned `""` when the rendered child existed but was
    empty (no fall-through to the whole-clone text).
  - Runtime was not configured with `requireBothRoles: true`, so the
    partial turn list made it past `capture-runtime.ts:306`.
  Both are now closed: `extractText` falls through when the rendered child
  is empty, and `requireBothRoles: true` is wired on the Copilot runtime
  (matching Zhipu, Poe, Grok, M365-Copilot). 2 new regression tests in
  `__tests__/copilot.content.test.ts`.
- **Copilot `/share/<id>` URL not skipped (MCP4).** Read-only shared
  conversations at `copilot.microsoft.com/share/<id>` were captured as if
  authored by the current user. `extractMessages` now takes a `pathname`
  argument and short-circuits to `[]` on `^/share/` (mirrors commit
  `702bf0e`, Gemini G8 + Claude C9). 3 new regression tests.

### Fixed — Chrome Web Store listing and metadata

The 2026-04-22 rejection triggered a pass across every reviewer-facing
surface for accuracy and consistency. Specifically:

- **Listing description (EN + ZH).** Removed the bullet list of 14 AI
  brand names with URLs that triggered the keyword-spam heuristic. The
  canonical list now lives in the public GitHub README (`#supported-ai-tools`
  anchor). Brand names that remain in the description appear in contextual
  prose, not as lists.
- **Listing description context-menu claim.** Was "right-click Save
  selection as snippet"; actual context menu at `background.ts:602` is
  "Capture This Page (PCE)". The "Save selection" feature is a floating
  DOM button on text selection (`text-collector.content.ts`), not a Chrome
  context menu. Description now describes both shortcuts accurately.
- **Listing description product form.** Was "PCE Core desktop app"; actual
  install flow per `README.md` is `python -m pce_core.server`. Replaced
  with "PCE Core companion server" / "local Python application" across
  listing.md, justification.md, and PRIVACY.md.
- **Privacy justifications `contextMenus` label.** Was "Save selection as
  snippet"; now "Capture This Page (PCE)" matching the code. Chrome Web
  Store reviewers compare justification strings to code verbatim — a
  mismatch triggers a ding.
- **Host-permissions justification count.** Was "14 hosts" in §3 and
  "17+ AI hosts" in the prose; the manifest actually declares 25 host
  patterns (17 F1 + 8 F2) covering ~14 AI services (multi-domain services:
  ChatGPT, Kimi, Notion, Figma). §3 now enumerates every manifest entry
  verbatim including previously-omitted `www.kimi.com`, `www.notion.so`,
  `www.figma.com`. `wxt.config.ts` comment updated to match.
- **Popup version label.** The toolbar popup's header showed `v0.3.0`
  while the manifest was `1.0.1`. Users reloading after install would see
  the mismatch. Now `v1.0.1`.
- **PRIVACY.md.** `Applies to: version 1.0.0 and later` → `1.0.1 and later`
  with a note that v1.0.0 was rejected. Removed the enumerated brand list
  in §2 (same keyword-spam pattern that got the listing rejected); now
  points to the manifest + README.
- **SECURITY.md contact.** Was `security@pce.example.com` with a
  "replace before public" placeholder note. RFC 2606 reserves example.com
  and reviewers / public reporters clicking through would see a
  non-functional contact. Replaced with GitHub's built-in Private
  Vulnerability Reporting URL, which routes privately to the repo owner
  without needing a separate mailbox. PRIVACY.md contact sections
  updated to match.

### Fixed — documentation hygiene

- 4 stale "⬜ OPEN" markers in coverage-diff docs synced to actual code
  state:
  - PX1 (Perplexity dedup slice-collapse) → CLOSED (commit predates v1.0.0)
  - PX2 (Perplexity isStreaming gate) → CLOSED
  - DS1 (DeepSeek isStreaming gate) → CLOSED
  - M365-P1 (M365 Copilot isStreaming gate) → CLOSED
- `SUBMISSION-PLAYBOOK.md` Step 0 rewritten: marks version bump + zip
  rebuild as DONE (with commit hashes), lists the 4 side-effect fixes
  shipped in v1.0.1, emphasizes the smoke test is now load-bearing (not
  metadata-only), and corrects stale "17 hosts" to "25 host patterns".
- All `Docs/store/*.md` titles refreshed from `(v1.0.0)` to `(v1.0.1)`.
- `Docs/stability/P5B-PLAN.md` top context updated with the rejection
  narrative and v1.0.1 rebuild summary.

### Tests

- 621/621 unit tests pass (up from 616 at the start of the resubmission
  audit; +5 regression tests: 2 for MCP6, 3 for MCP4).
- Webstore zip: `pce-browser-extension-wxt-1.0.1-chrome.zip` (1.01 MB).
  Manifest verified: 25 host patterns, 0 `<all_urls>` in `content_scripts`,
  clean description without brand enumeration, version 1.0.1.

## [1.0.0] - 2026-04-21 — rejected, never published

Submitted to the Chrome Web Store on 2026-04-21. Rejected on 2026-04-22
with transfer ID `FZSL` (Yellow Argon keyword-spam heuristic). Never
reached the Chrome Web Store front-end, therefore never installed by any
user. The rejection root cause and fix history are documented above and
in `Docs/store/listing.md` → "Rejection history".

The code shipped inside the v1.0.0 zip is otherwise equivalent to v1.0.1
minus the Copilot MCP4 + MCP6 fixes and the popup version label change.

## Unreleased

Planned for v1.0.2 (no commitments, subject to live-user evidence):

- Claude / Gemini / DeepSeek `requireBothRoles` defense (see
  `Docs/stability/REQUIREBOTHROLES-AUDIT.md`).
- Remaining open items in per-site coverage diffs:
  `COPILOT-COVERAGE-DIFF.md` MCP2 (SPA nav hook) and MCP5 (Bing citation
  structured extraction), `PERPLEXITY-COVERAGE-DIFF.md` PX3–PX6,
  `DEEPSEEK-COVERAGE-DIFF.md` DS2–DS6, `M365-COPILOT-COVERAGE-DIFF.md`
  M365-P2–P4.
- Live autopilot runs for S1 (ChatGPT / Claude / Gemini / GAS) and S2
  (Copilot / M365 / Perplexity / DeepSeek) once the user completes
  bootstrap + login.
