# `requireBothRoles` audit — cross-site partial-capture defense

**Date:** 2026-04-22
**Trigger:** Copilot empty-reply bug (P5B-PLAN P0, closed as MCP6 in `COPILOT-COVERAGE-DIFF.md`, commit `7e80ef4`).
**Scope:** every Tier-1 / Tier-2 content script in `pce_browser_extension_wxt/entrypoints/`.

## Background

`createCaptureRuntime` accepts a boolean option `requireBothRoles`. When `true`, the runtime (at `capture-runtime.ts:306`) defers capture any time the extracted message list contains only one role — preventing the "user sent a message, assistant didn't reply yet" partial state from being emitted as a finalized capture event.

The legacy Copilot script did NOT set this option, which let a `user`-only turn list through the gate any time Copilot's React UI rendered the user turn before the assistant's `.ac-textBlock` populated. Users observed this as an **empty-reply capture** in the dashboard.

The rest of this file audits whether other sites carry the same risk.

## Audit result

| Site | `requireBothRoles` set? | Native partial-capture defense? | Risk | Action |
|---|---|---|---|---|
| `chatgpt.content.ts` | ❌ | ✅ Strategy A has `hasNonUser && hasAssistantTurn` fall-through (line 240-245) that rejects user-only results when an assistant turn exists in DOM | LOW | None. Existing defense is equivalent-ish; add `requireBothRoles` only if a live user reports an empty-reply symptom. |
| `claude.content.ts` | ❌ | ❌ None visible | MEDIUM | Defer. No user reports. Track as v1.0.2 candidate. Adding it is a 1-line + 1-test change. |
| `gemini.content.ts` | ❌ | ❌ None visible | MEDIUM | Same as Claude. |
| `copilot.content.ts` | ✅ (just added) | N/A | — | Closed in `7e80ef4`. |
| `grok.content.ts` | ✅ | — | — | Already protected. |
| `m365-copilot.content.ts` | ✅ | — | — | Already protected. |
| `poe.content.ts` | ✅ | — | — | Already protected. |
| `zhipu.content.ts` | ✅ | — | — | Already protected. |
| `google-ai-studio.content.ts` | ❌ | ❌ None visible | LOW | GAS is a developer playground — single-turn prompts are a legitimate use case (user tests a prompt, doesn't care if assistant replied). Enabling `requireBothRoles` might silently drop those. Leave off; revisit if a user reports empty-reply. |
| `deepseek.content.ts` | ❌ | ❌ None visible | MEDIUM | Same as Claude / Gemini. Track as v1.0.2 candidate. |
| `perplexity.content.ts` | ❌ | ❌ None visible | LOW | Perplexity's "answer" concept is always both-sides (query + sources + answer). Low risk in practice. |
| `huggingface.content.ts` | ❌ | ❌ None visible | LOW | Niche audience, no reports. |
| `manus.content.ts` | ❌ | ❌ None visible | LOW | Same. |
| `figma.content.ts` / `notion.content.ts` / `gmail.content.ts` / `generic.content.ts` | ❌ | varies | N/A | Tier-2 scaffolding — capture pipeline not reliable enough for `requireBothRoles` to be the relevant knob yet. |

## Decision for v1.0.1 (resubmission package)

- Copilot fix ships. 1 site, 1 known bug, 1 minimal change.
- No blanket rollout to Claude / Gemini / DeepSeek despite the theoretical risk — applying it without evidence risks silently dropping legitimate single-turn captures, and the current `isStreaming` gate in those files (added earlier in the same session) already defends the streaming-window portion of the bug.

## Future work (v1.0.2 candidates, no commitment)

- **Claude MCP6-equivalent.** If any live user reports "claude.ai capture shows user message without assistant reply", apply the same 1-line fix + regression test.
- **Gemini MCP6-equivalent.** Same.
- **DeepSeek MCP6-equivalent.** Same, after Copilot's fix ships and we have a week of real traffic to confirm no false-negative from the gate.

Don't preemptively roll this out — the user who files the MCP6-equivalent issue is the signal that the speculative risk is real on that specific site's React layout, and their bug report will also tell us the exact DOM pattern to add to the regression test.
