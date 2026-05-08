# Perplexity Full Coverage Specification & Collaboration Protocol

**Scope:** this document defines what it means for PCE to capture
high-fidelity Perplexity usage on `www.perplexity.ai`, audits the
current implementation, and maps the site to a mechanical E2E matrix.

**Status:** **S1 tier** - high-value frequent use for the $50+/mo
AI-native power user persona. This file supersedes
`PERPLEXITY-COVERAGE-DIFF.md` as the canonical Perplexity spec. Parts
IV-VI inherit from `CHATGPT-FULL-COVERAGE.md`.

**Timebox:** first full-runner pass should be one Perplexity profile
session, then targeted reruns with `PCE_PERPLEXITY_CASES=Pxx,...`.

---

## Part I - What "full coverage" means

Perplexity is an answer/search engine first and a chat app second. The
unit we need to preserve is a cited search thread: user query,
assistant synthesis, source citations, selected mode/model when visible,
attachments, and follow-up context. Non-conversation surfaces must stay
silent.

### I.1 Product surfaces PCE must handle

| # | Surface | URL shape | What the user sees | What PCE must capture |
|---|---|---|---|---|
| 1 | Quick / default search | `/`, then `/search/<id>` or `/thread/<id>` | Short cited answer | user query + assistant answer |
| 2 | Pro Search | same | deeper answer, more sources, optional model choice | turns + citations; model if visible |
| 3 | Research mode | same | long multi-step report | final report once, with citations |
| 4 | Streaming answer | same | progressive answer, stop/pause affordance | no partial capture while streaming |
| 5 | Follow-up in same thread | same id | context-aware second query | appended turn in same session |
| 6 | Related questions | same id | clickable suggested follow-ups | clicked suggestion becomes a user turn |
| 7 | Code answer | same id | fenced code block | `code_block` attachment |
| 8 | PDF/text/code upload | same id | file chip above prompt | user `file` attachment |
| 9 | Image upload | same id | image thumbnail or chip | user `image_url` attachment |
| 10 | Audio/video upload | same id | media file attachment | user `file` attachment; transcript in answer when available |
| 11 | Source/focus modes | mode selector | Web / Academic / Social / Finance / Files | mode metadata when visible; normal turns |
| 12 | Model selector | model selector | Best / Sonar / GPT / Claude / Gemini etc. | `model_name` when visible |
| 13 | Space thread | `/space/<sid>/thread/<id>` or `/spaces/<sid>/thread/<id>` | workspace-scoped thread | stable session id, no collision with normal threads |
| 14 | Space landing / assets | `/space/<sid>` or `/spaces/<sid>` | files, shared threads, collaborators | no capture unless a chat thread is active |
| 15 | Shared/read-only thread | `/share/*` or `/search/<id>?s=<token>` | public or shared content | no capture of read-only history |
| 16 | History / Library | `/library`, history sidebars | saved thread list | no capture of list chrome |
| 17 | Settings / Profile | `/settings*`, `/account*`, `/profile*` | account, plan, privacy controls | no capture |
| 18 | Error / quota states | thread | rate-limit, network, policy, unavailable model | no assistant message made from error banners |
| 19 | Create files and apps | mode selector / `/labs` family | reports, charts, files, apps, app tab | final user/assistant thread; generated assets as attachments when schema supports them |
| 20 | Image generation | Pro media flow | generated images | assistant `image_generation` or `image_url`; skip if account gated |
| 21 | Video generation | Pro/Max media flow | generated video with share/download/regenerate | assistant media attachment when schema supports it; entry-only if expensive mode disabled |
| 22 | Export/share modals | thread controls | PDF/doc/share dialogs | no duplicate capture from modal chrome |
| 23 | App connectors / cloud files | connector picker | Drive/Dropbox/OneDrive etc. | no capture from picker; file/source attachment when used in a thread |
| 24 | Enterprise/Max gates | plan modal, org policy | paywall or unavailable feature | skip with evidence; never capture paywall as assistant |

### I.2 Official source map

Checked 2026-04-25 against Perplexity-owned documentation:

- How Perplexity works, citations, follow-ups, model selector:
  `https://www.perplexity.ai/help-center/en/articles/10352895-how-does-perplexity-work`
- Pro Search, source/search modes, code interpreter, model choice:
  `https://www.perplexity.ai/help-center/en/articles/10352903-what-is-pro-search`
- Research mode, report generation, export/share:
  `https://www.perplexity.ai/help-center/en/articles/10738684-what-is-research-mode`
- File uploads, image/audio/video attachments:
  `https://www.perplexity.ai/help-center/en/articles/10354807-file-uploads`
- Spaces, threads, files, collaboration, source selection:
  `https://www.perplexity.ai/help-center/en/articles/10352961-what-are-spaces`
- Create files and apps, generated files/apps/images/charts:
  `https://www.perplexity.ai/help-center/en/articles/11144811-perplexity-create-files-and-apps`
- Pro plan media/model/file benefits:
  `https://www.perplexity.ai/help-center/en/articles/10352901-what-is-perplexity-pro`
- Video generation:
  `https://www.perplexity.ai/help-center/en/articles/11985060-generating-videos-with-perplexity`

### I.3 Meta-capture invariants

- Role accuracy: user queries must never be stored as assistant output.
- Citation preservation: answer-body citation chrome may be stripped from
  text, but external source links must survive as `citation` attachments.
- Streaming safety: defer capture until the visible stop/pause affordance
  disappears and answer text is stable.
- SPA correctness: `/search/A` to `/search/B` or Space thread changes must
  reset fingerprint state immediately or through polling.
- Session identity: normal threads use thread/search id; Space threads
  include the Space id in the hint to avoid collisions.
- No idle noise: Library, Settings, Space landing, share, connector pickers,
  export modals, and paywalls must produce zero captures.
- Account honesty: Pro/Max/Enterprise features can skip with evidence; they
  must not be marked pass from a paywall screenshot.
- Generated-media semantics: generated images/videos are assistant assets,
  not user uploads.

---

## Part II - Current implementation audit

Grounded in:

- `pce_browser_extension_wxt/entrypoints/perplexity.content.ts`
- `pce_browser_extension_wxt/entrypoints/__tests__/perplexity.content.test.ts`
- `tests/e2e/sites/perplexity.py`
- `tests/e2e/test_perplexity_full.py`

### II.1 Extraction strategy

| Strategy | Selector / heuristic | Tested | Surfaces |
|---|---|---|---|
| 1 | `[class*="ThreadMessage"]` | yes | modern thread turns |
| 2 | `[class*="thread-message"]` | yes | lowercase class variant |
| 3 | `QueryBlock` / `AnswerBlock` | yes | block-based layout |
| 4 | `query-block` / `answer-block` | partial | legacy block layout |
| 5 | `[data-testid*="message"]` | yes | test-id layout |
| 6 | `[class*="prose"]` | yes | rendered answer fallback |
| Fallback | pair query/question nodes with answer/prose nodes by index | yes | emergency extraction |

### II.2 Helper coverage

| Helper | Current status | Handles |
|---|---|---|
| `getSessionHint` | upgraded | `/search`, `/thread`, Space thread, share/null, non-chat/null |
| `getContainer` | implemented | `main`, thread/search result containers, body fallback |
| `detectRole` | implemented | query/question/user vs answer/response/prose |
| `extractText` | implemented | strips citations/actions/related chrome from plain text |
| `extractAttachments` | shared | files, images, code, citations, canvas/tool output |
| `isStreaming` | implemented | shared streaming helper plus stop/cancel/pause buttons |
| `getModelName` | best-effort | visible model labels only; may be null |
| `getLayerMeta` | best-effort | visible mode/source labels only |
| `hookHistoryApi` | enabled | immediate SPA thread reset |
| `requireSessionHint` | enabled | suppresses Library/Settings/share/Space landing noise |

### II.3 Known gaps

| ID | Gap | Status |
|---|---|---|
| PX1 | Long-prefix dedupe collapse | closed: full role+content key |
| PX2 | Mid-stream partial capture | closed: streaming gate wired |
| PX3 | Model selector may not be visible in all plans/locales | open, best-effort metadata |
| PX4 | `/share` and `?s=` read-only skip | closed in helper/unit tests; needs live proof |
| PX5 | Citation extraction depends on DOM links being inside the answer turn | open until P14 live pass |
| PX6 | SPA history hook disabled | closed: hook enabled |
| PX7 | Create files/apps and generated video schema depth | open; entry/evidence first, richer schema later |
| PX8 | Space creation/destructive cleanup | open; runner uses existing/env Space unless explicitly allowed |

---

## Part III - Live validation matrix

Run all:

```powershell
python -m pytest tests/e2e/test_perplexity_full.py -v -s
```

Run a subset:

```powershell
$env:PCE_PERPLEXITY_CASES="P01,P14,P20"; python -m pytest tests/e2e/test_perplexity_full.py -v -s
```

Reports are written under `tests/e2e/reports/perplexity/<timestamp>/`.

| Case | Surface | Tier | Action | Mechanical pass condition |
|---|---|---|---|---|
| P01 | Quick/default search | free_core | send single cited query | session has user+assistant and token |
| P02 | Streaming answer | free_core | long answer | no partial; final session has token |
| P03 | New thread | free_core | start fresh thread | new session id and token |
| P04 | Follow-up | free_core | second query same thread | same recent session has >=4 messages |
| P05 | Code block | free_core | ask for fenced code | assistant `code_block` attachment |
| P06 | Pro Search mode | pro_core | select Pro/Search | user+assistant, citations preferred |
| P07 | Model selector | pro_core | choose visible model | response captured; model evidence in report if visible |
| P08 | Academic source | free_or_pro_mode | select Academic | response captured; mode evidence |
| P09 | Finance/SEC source | free_or_pro_mode | select Finance/SEC | response captured; mode evidence |
| P10 | Social source | free_or_pro_mode | select Social/Reddit | response captured; mode evidence |
| P11 | PDF upload | pro_upload | attach PDF | user `file` attachment |
| P12 | Image upload | pro_upload | attach image | user `image_url` attachment |
| P13 | Audio/video upload | optional_upload | attach media | user `file` attachment or skip by fixture/account |
| P14 | Citations | free_core | factual current-ish query | assistant `citation` attachment |
| P15 | Related question | free_core | click suggestion | added user+assistant turn |
| P16 | Research mode | research | select Research | long answer/report, screenshot evidence |
| P17 | Create files/apps | pro_create | mode entry or run when allowed | entry screenshot or captured final thread |
| P18 | Space thread | space_surface | existing/env Space thread | captures Space-scoped session id |
| P19 | Shared thread | manual_url | open share URL | zero new captures |
| P20 | Library/History | no_capture | open library | zero new captures |
| P21 | Settings/Profile | no_capture | open settings/profile | zero new captures |
| P22 | Error/quota | free_error_state | controlled offline send | no assistant error-banner capture |
| P23 | Image generation | pro_media | prompt image generation | assistant generated image attachment or gated skip |
| P24 | Video generation | pro_media_expensive | prompt video or entry-only | media attachment if enabled; otherwise no-capture entry evidence |

### III.1 Evidence standards

- `pass`: API assertions show correct roles/attachments/text or a negative
  page produced no new captures.
- `skip`: account/plan/region/quota/UI gate proven in JSON/screenshot.
- `fail`: action was possible but capture, session, role, or attachment
  assertion failed.
- Visual review cases must include before/after PNG paths and a valid PNG
  assertion; semantic judgement can be done by the agent from screenshots.

### III.2 Current evidence

No full Perplexity live run has been recorded yet for this spec. The
previous smoke runner proved basic Perplexity capture shape only. First
live full pass should start with:

```powershell
$env:PCE_PERPLEXITY_CASES="P01,P02,P05,P14,P20,P21"; python -m pytest tests/e2e/test_perplexity_full.py -v -s
```

Then expand to upload, Research, Space, and media cases.

---

## Parts IV-VI - inherited protocol

Use the same collaboration loop, reporting convention, and fix/rerun
discipline as `CHATGPT-FULL-COVERAGE.md`:

1. Run a bounded case subset.
2. Read `summary.json`, failed case JSON, and screenshots.
3. Patch content script / adapter / verifier with a unit regression.
4. Run unit tests and targeted pytest subset.
5. Commit only after the local and live evidence for that round is clear.
