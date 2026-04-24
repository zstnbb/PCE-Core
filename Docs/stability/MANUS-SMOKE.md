# Manus — S3 Smoke Coverage

**Tier:** **S3** — breadth coverage. Emerging general-purpose
agent platform (browse / plan / execute). Early-adopter slice of
the $50+/mo AI-native power user persona uses it; treated as
breadth coverage until persona penetration stabilises. Canonical
tier definition: `Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited)

## Surfaces (abbreviated)

- Agentic chat: user gives a high-level goal, Manus plans + runs
  sub-tasks and reports back. The "turn" is coarser than a chat
  message (user goal → assistant multi-step report).
- URL shape: `manus.ai` → `manus.ai/app/<id>` or similar.
- Turn DOM: agent-report bubbles with nested tool-call blocks;
  extracted via site-specific script.
- No Canvas / Artifacts / Projects / custom-GPT store in the
  ChatGPT sense.

## T-cases

| ID | Description | Pass condition |
|---|---|---|
| `T01` | Vanilla goal → report | user goal + assistant multi-step report captured under `provider="manus"` |
| `T02` | Streaming / progressive report | no partial captures mid-plan; final capture after agent settles |
| `T20` | Settings silent | `/settings*` produces zero captures |

## Current implementation

- **Content script:** `pce_browser_extension_wxt/entrypoints/manus.content.ts`
- **E2E adapter:** `tests/e2e/sites/manus.py` (smoke only)
- **E2E runner:** `tests/e2e/test_capture.py` (smoke matrix)

## Known gaps

- Agentic "turn" semantics don't perfectly match the
  user/assistant bubble model. Nested tool calls inside the
  assistant report may be captured as plain text rather than
  structured `tool_call` / `tool_result` attachments. Acceptable
  for S3; revisit if persona penetration rises.
- Long-running agent sessions may exceed the current streaming
  gate's settle timeout on slow plans — live probe needed to tune.
