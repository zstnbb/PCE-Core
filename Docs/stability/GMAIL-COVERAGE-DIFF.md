# Gmail "Help me write" — Coverage Diff vs CHATGPT-FULL-COVERAGE

> ⚠ **NOT READY — scaffolding only.** This is an **SX** tier
> stub under the 2026-04-25 realignment. The content script
> exists but **no live-DOM probe has happened** against a real
> Google Workspace account where the "Help me write" / Gemini-in-
> Gmail features are enabled. Selectors below are informed
> guesses. Do not treat this spec as validated coverage.

**Tier:** **SX** — scaffolding (F2 SaaS-embedded AI, Workspace
gated). Out-of-scope for the $50+/mo AI-native power user
persona's core capture obligations; relevant only to the
Workspace business slice. Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**E2E depth:** none — blocked on Workspace account + DOM probe
**Ship target:** v1.1 earliest (depends on probe + adapter)

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

Gmail's AI is surfaced through:

| ± | Surface | Notes |
|---|---|---|
| + | "Help me write" in the compose window | Pencil / sparkle icon → AI drafts an email body given a short prompt |
| + | Gemini side panel in Gmail | Summarise thread / suggest reply / schedule help; shares DOM with Google Workspace Gemini panel |
| + | Smart Compose inline | Gray ghost text as the user types; NOT an AI invocation in the capture sense (pre-exists Gemini) |
| + | Smart Reply chips | One-tap reply suggestions; captured only when a user accepts and sends |
| − | No standalone chat URL | Always embedded |
| ≠ | Session hint | Thread ID from URL (`#inbox/<id>`) |
| ≠ | Turn DOM | Compose popup DOM + side-panel DOM; both are iframes or shadow-DOM in places |

## 2. Share-able T-cases (aspirational — all need live probe)

- `T01` "Help me write" invocation as a turn
- `T20` Pure email read/send silence (no AI invoked)

## 3. Site-specific T-cases (new, pending probe)

| ID | Description | Pass condition |
|---|---|---|
| `GM1` | "Help me write" in compose | user prompt ("Thank the client for the meeting…") + assistant draft captured; session_hint = thread ID or `compose-<ts>` when no thread exists |
| `GM2` | Gemini side panel summarise | side-panel Q&A captured under the current thread ID |
| `GM3` | Smart Reply accept | selected suggestion captured as an assistant turn with `source_type: "smart_reply"` if DOM exposes it |
| `GM4` | No-AI inbox scroll | ordinary inbox browsing / email read produces ZERO captures |
| `GM5` | `requireBothRoles` behaviour | no capture fires until both user prompt and AI draft are both present (defends against partial renders) |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/gmail.content.ts` (scaffolding — ~7 KB)
- **E2E adapter:** ⬜ NOT CREATED
- **Host match:** `mail.google.com`
- **What the script tries today:** guess-selectors for the compose popup + Gemini side panel. Likely mostly misses until live probe.

## 5. Known gaps (blocking ship)

- **GM-P1.** No live-DOM probe. Status: ⬜ BLOCKING — needs user
  with Workspace account + Gemini-in-Gmail enabled + 30-60 min
  inspection.
- **GM-P2.** No E2E adapter. Status: ⬜ BLOCKING — depends on
  GM-P1.
- **GM-P3.** Privacy: Gmail content is maximally sensitive. Must
  confirm that only the AI-invocation delta is captured
  (prompt + draft), never the full thread body or recipient
  addresses. Status: ⬜ DESIGN DECISION PENDING — likely
  default-off until an explicit per-site consent toggle exists.
- **GM-P4.** Shadow DOM / iframe boundaries: "Help me write" may
  render inside a shadow root; the selector ladder must use the
  iframe / shadow escape helpers from `pce-dom.ts`. Status:
  ⬜ OPEN.
- **GM-P5.** Smart Compose (the ghost-text autocomplete) should
  NOT be captured; it's not a discrete AI invocation. Confirm
  the content script doesn't fire on it. Status: ⬜ OPEN.

## 6. What must happen before this becomes S2 or S3

1. User enables Gemini-in-Gmail, records 15 minutes of "Help me
   write" + side-panel usage, shares DOM snapshots and captures.
2. Cascade turns observations into a pinned selector set + tests.
3. Privacy default decision: should Gmail capture be **off by
   default** with an explicit opt-in? (Recommend yes.)
4. Re-probe, round-trip captures, then promote to S3 or S2.

Until then, the Store listing must not imply Gmail AI capture
works.
