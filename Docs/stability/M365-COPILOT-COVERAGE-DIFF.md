# Microsoft 365 Copilot — Coverage Diff vs CHATGPT-FULL-COVERAGE

**Tier:** S2 (Office-Web embedded AI — F2 form factor)
**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**Ship target:** v1.0.2

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

M365 Copilot is **not** a chat site — it's a side panel inside Word/
Excel/PowerPoint/Outlook web apps. Coverage model differs.

| ± | Surface | Notes |
|---|---|---|
| + | Side-panel chat in Word / Excel / PowerPoint / Outlook web | Each host app has its own Copilot panel |
| + | "Draft with Copilot" inline | In-document prompts (different DOM from side panel) |
| + | Email-compose Copilot | Outlook-specific |
| − | No standalone chat URL | Always embedded |
| − | No Custom GPTs / Projects / Canvas | |
| − | No model selector exposed in DOM | Model identity captured via MITM (L1 network capture) |
| ≠ | Session hint | Per-document GUID from URL query (`?sourcedoc=%7B<GUID>%7D`) plus `/copilot/chat/<id>` |
| ≠ | Turn DOM | ChatGPT-style `[data-message-author-role]` when present; fallback to class keywords and aria-labels |

## 2. Share-able T-cases (reused from ChatGPT / parent)

- `T01` vanilla chat (in Word side panel)
- `T02` streaming
- `T05` code / structured reply (Excel formula suggestions use fenced blocks)
- `T20` settings / non-Copilot page negative-capture

## 3. Site-specific T-cases (new)

| ID | Description | Pass condition |
|---|---|---|
| `M1` | Side-panel chat in Word Online | user prompt + assistant reply captured with document GUID as `session_hint` |
| `M2` | Side-panel chat in Excel Online | same as M1 but under Excel host |
| `M3` | Document non-chat scroll | ZERO new captures (panel closed) |
| `M4` | requireBothRoles behaviour | no capture fires until BOTH user and assistant are present (defends against loading-state partials) |
| `M5` | Outlook email-compose Copilot | inline Copilot prompt + suggested draft captured |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/m365-copilot.content.ts` (227 lines)
- **Unit tests:** `pce_browser_extension_wxt/entrypoints/__tests__/m365-copilot.content.test.ts` (18 tests)
- **E2E adapter:** ⬜ NOT YET CREATED (no `tests/e2e/sites/m365_copilot.py`)
- **Key helpers present:** `normalizeText` with UI noise filter, `getSessionHint` with doc GUID, `requireBothRoles: true` (good — guards against partial render)
- **Missing helpers:** `getModelName`, `isStreaming` gate, `/share/` skip
- **Host matches:** `m365.cloud.microsoft`, `*.cloud.microsoft`, `*.officeapps.live.com`

## 5. Known gaps (short list)

- **M365-P1.** No `isStreaming` gate. Mid-stream partials possible when user closes the panel mid-reply. Status: ⬜ OPEN.
- **M365-P2.** No `getModelName` — `conversation.model_name` always empty. Status: ⬜ OPEN — falls back to MITM for canonical model identity, but dashboard shows `null`.
- **M365-P3.** `hookHistoryApi: false`. Navigating between Word↔Excel↔Outlook in the same tab: new panel mounts but polling takes up to 5s. Status: ⬜ OPEN.
- **M365-P4.** No E2E adapter; can't run autopilot yet. Status: ⬜ OPEN — needs ~300 lines of `m365_copilot.py`.
- **M365-P5.** `TURN_SELECTORS` covers 4 variants but M365 ships DOM churn every month; may need live-DOM probe every v1.x. Status: ⬜ MONITORING.

## 6. Order of attack

```
Block 0 — build E2E adapter (60 min):  m365_copilot.py (navigate per host, detect panel, send via panel input)
Block 1 — smoke (5 min):               M1 (Word side panel vanilla)
Block 2 — streaming (5 min):           T02-equiv with M365-P1 fix
Block 3 — cross-host (15 min):         M2 (Excel), M5 (Outlook), M3 (panel-closed silence)
Block 4 — role guard (5 min):          M4 (requireBothRoles)
```

Total ~90 min first pass (building the adapter dominates).

## 7. Shared bug assumptions

- `isStreaming` fix mirrors G2/C2 — 3-line change.
- Attachment capture likely NOT relevant (M365 Copilot rarely emits rich attachments in chat panel — graphics go into the document body, not the panel).
- The biggest risk is DOM churn: Microsoft rewrites the panel every few months. Strategy 5 monitoring is more important than extractor completeness.
