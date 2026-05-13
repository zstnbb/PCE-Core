# P4 Windsurf — Product Matrix

> **Status**: Stage 4 in progress (normalizer shipped, live-verified)
> **Tier**: D1 (high value, reuses L1 infrastructure)
> **Primary capture path**: L1 TLS MITM + NODE_EXTRA_CA_CERTS injection
> **Domain**: `server.self-serve.windsurf.com` (post-Cognition acquisition)
> **Normalizer**: `WindsurfCascadeNormalizer` + `WindsurfManagementNormalizer`
> **Install guide**: `Docs/install/PCE_WINDSURF_INSTALL.md`

---

## §1 Mission

Capture all Windsurf Cascade AI conversations (user prompts, assistant
thinking, assistant responses, model names, tool calls) into PCE's local
database. Windsurf is the second most-used AI IDE in the target persona
(after Cursor), and its Cascade agent produces rich multi-step trajectories
that are valuable for replay and analysis.

---

## §2 Surface inventory

| Surface | Captured | Method | Notes |
|---|---|---|---|
| Cascade Chat (Write mode) | ✅ | L1 gRPC | Full conversation via RecordCortexTrajectoryStep |
| Cascade Chat (Chat mode) | ✅ | L1 gRPC | Same endpoint as Write mode |
| Cascade Thinking | ✅ | L1 gRPC | Assistant reasoning in trajectory steps |
| Model selection | ✅ | L1 gRPC | From RecordCortexGeneratorMetadata |
| Autocomplete (Tab) | 🟡 | L1 gRPC | Endpoint TBD (not yet mapped) |
| Command (Ctrl+I) | 🟡 | L1 gRPC | Likely same trajectory pipeline |
| Devin integration | 🟡 | WebSocket | app.devin.ai ACP — needs schema extension |
| Memories | ❌ | — | Encrypted local storage |
| Management plane | ✅ | L1 gRPC | User identity, models, plan (existing normalizer) |

---

## §3 UCS / plane mapping

| Plane | Layer | Status | Notes |
|---|---|---|---|
| **N (Network)** | **L1 + NODE_EXTRA_CA_CERTS** | ✅ Primary | Full Cascade capture |
| H (Host/Local) | L3g | ❌ | Encrypted .pb files |
| M (MCP) | L3f | 🟡 Fallback | Official support, limited coverage |
| U (UI) | L3d/L4b | — | Not needed (L1 sufficient) |

---

## §4 Tier positioning

**D1** — High value, lower marginal risk than P3 Cursor because:
- Same L1 infrastructure (mitmproxy + NODE_EXTRA_CA_CERTS)
- Simpler protobuf extraction (plaintext strings in wire format)
- No binary protobuf decoding needed (unlike Cursor's gRPC)

---

## §5 Case grid

### §5.A Chat region (Cascade Write + Chat mode)

| Case ID | Title | Acceptance | Status | Notes |
|---|---|---|---|---|
| W01 | vanilla user→assistant | `messages` table has ≥1 `user` row + ≥1 `assistant` row with non-empty `content_text` for the trajectory session | ✅ PASS | Live verified 2026-05-13 |
| W02 | model name extraction | `messages.model_name` = `swe-1-6-slow` (or current default) for assistant rows | ✅ PASS | Extracted from protobuf |
| W03 | multi-turn conversation | Same `session_id` for all messages in one trajectory (trajectory_id as session_key) | ✅ PASS | trajectory_id grouping works |
| W04 | trajectory_id session key | `sessions.session_key` matches the UUID trajectory_id from protobuf | ✅ PASS | — |
| W05 | thinking capture | Assistant `interaction_kind='thinking'` rows contain reasoning text | ✅ PASS | "The user is asking..." |
| W06 | code generation | Code blocks in assistant response captured as content_text | ✅ PASS | Live verified: fibonacci function |
| W07 | tool use (file edit) | Cascade Write mode file edits appear as trajectory steps | 🟡 PENDING | Needs Write mode test |
| W08 | error handling | Rate limit / error messages captured with error context | ✅ PASS | `resource_exhausted` captured |
| W09 | management metadata | GetUserStatus / GetCliTeamSettings parsed into layer_meta | ✅ PASS | email, plan, models |
| W10 | dedup on re-capture | Same trajectory step captured multiple times → single message row (enriched, not duplicated) | ✅ PASS | message_hash dedup works |

### §5.B Open architectural questions

- **Q1**: `GetChatMessage` bodies are encrypted — is this the actual streaming channel? **Not blocking** — trajectory steps already contain full content.
- **Q2**: WebSocket `app.devin.ai/api/acp/live` carries Devin integration data — needs DB schema extension for WebSocket direction. **Deferred to P6**.
- **Q3**: Autocomplete (Tab) endpoint not yet identified. **Not blocking v1.x** — chat is priority.

---

## §6 Release gates

- **Ship bar**: ≥ 7/10 cases PASS (currently 8/10 = 80% ✅ SHIP-READY)
- **No regressions**: existing 137+ tests must stay green
- **Install doc**: ✅ shipped

---

## §7 Known limitations

- Requires `NODE_EXTRA_CA_CERTS` injection (user must launch Windsurf from PCE-aware terminal)
- Encrypted local storage means no offline/historical capture (L3g not viable)
- WebSocket (Devin ACP) not yet captured (DB schema constraint)
- Autocomplete not yet mapped

---

## §8 Reference handoffs

- Desk Research: `Docs/research/2026-05-13-windsurf-desk-research.md`
- RECON Findings: `Docs/research/2026-05-13-windsurf-recon-findings.md`
- Install Guide: `Docs/install/PCE_WINDSURF_INSTALL.md`
- S2 Harvest: `Docs/harvest/HARVEST-SESSION-S2-RESULTS.md` §2
