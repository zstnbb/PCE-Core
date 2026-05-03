# PCE Probe E2E Execution Standard

Version: pce-probe-e2e-standard-2026-05-04

This is the execution bar for the first-round browser-extension probe
matrix: ChatGPT, Claude, Gemini, Grok, and Google AI Studio. The runner
must use the already logged-in Chrome profile with the extension loaded.
Do not move the run to a fresh profile. If a rebuilt extension is needed,
reload the existing extension/profile through the probe reload path.

## Global Pass Bar

Every PASS must prove all three phases:

- Capture: the browser extension observes the current visible state and
  emits a token-bearing `PCE_CAPTURE` or a documented equivalent event.
- Storage: PCE Core exposes the normalized session/message state through
  `/api/v1/sessions` and `/api/v1/sessions/{id}/messages`.
- Render: the stored state contains the text or `content_json.rich_content`
  contract that the dashboard can render without relying on raw provider
  DOM or transient network payloads.

SKIP is allowed only for external/account conditions: not logged in,
provider quota exhausted, provider rate limit, feature not available on
that account/model, or a site that genuinely does not expose the tested
native surface. Selector drift, missing capture, missing storage, missing
render metadata, or a partial content-only proof is not a PASS.

The capture layer must remain layered. A site adapter may use DOM tokens,
runtime capture events, PCE Core session reads, content_json inspection,
and dashboard/render-contract checks together. No case should depend on a
single selector, a single event kind, or only the latest flat message row
when the user-facing behavior is structured.

## Upgraded Structured Cases

### T08 Regenerate

Regenerate is complete only when the whole regenerated-turn lifecycle is
represented:

- Capture: the regenerate click fires on the latest assistant turn and a
  fresh capture lands for the same user prompt.
- Storage: PCE Core stores at least two assistant variants for one logical
  prompt and preserves their relation with a stable structure such as
  `variant_group_id`, `variant_index`, `regenerated_from`, or an equivalent
  `content_json.rich_content.variant` contract.
- Render: the dashboard contract can identify the variant group and current
  variant, so a renderer can show/switch variants rather than flattening
  them as unrelated messages.

Text changed, two captures, or two assistant messages are content-level
signals only. They are not enough for T08 PASS under this standard.

### T09 Branch Flip

Branch flip is complete only when the current branch state is represented:

- Capture: after editing a user turn and flipping back/forward, the capture
  reflects the currently visible branch, not stale hidden branch DOM.
- Storage: PCE Core stores branch identity and parent/child relation, such
  as `branch_id`, `branch_index`, `parent_message_id`, `branch_choices`, or
  an equivalent `content_json.rich_content.branch` contract.
- Render: the dashboard contract can reconstruct the branch tree and the
  currently selected branch. Flat "latest user message has token A" is only
  a current-content signal and is not enough for PASS.

## Case Matrix Bar

| Case | Scenario | Capture bar | Storage bar | Render bar |
| --- | --- | --- | --- | --- |
| T00 | smoke | Probe and extension are attached to the logged-in profile. | PCE Core health is reachable. | Health/report metadata is available. |
| T01 | basic chat | User prompt and assistant reply are captured with token. | Session has user and assistant rows. | Text renders in dashboard message view. |
| T02 | streaming complete | Final streamed answer is captured after stop condition clears. | Stored assistant text is complete, not a partial prefix. | Final markdown/text renders without streaming artifacts. |
| T03 | stop streaming | Stop action is captured as the visible final state. | Stored assistant row reflects the stopped partial/final text. | Stopped output renders with no duplicate or stale continuation. |
| T04 | new chat URL | New chat/session transition is captured after durable URL/session id appears. | Session key is stable and not merged into previous chat. | Session list/detail opens the new chat correctly. |
| T05 | code block | Code block content and language are captured. | `content_json.rich_content.blocks` or equivalent code attachment is stored. | Dashboard renders code as a code block. |
| T06 | thinking model | Visible thinking/reasoning surface is captured when exposed. | Thinking is stored separately or tagged in rich content, not mixed into unrelated text. | Dashboard renders/hides thinking predictably. |
| T07 | edit user message | Edited prompt and resulting assistant state are captured. | Storage keeps both the edited user text and resulting assistant state. | Render shows the edited conversation state without stale prompt leakage. |
| T08 | regenerate | Fresh capture for same prompt after regenerate. | Variant relation is stored. | Variant group/current variant is renderable. |
| T09 | branch flip | Capture reflects the branch currently visible after flip. | Branch identity and parent relation are stored. | Branch tree/current branch is renderable. |
| T10 | PDF upload | File chip/paste/input upload and prompt are captured. | User message has file/document attachment metadata. | Dashboard renders a file/document card. |
| T11 | image upload | Image paste/input upload and prompt are captured. | User message has image attachment metadata. | Dashboard renders an image card or image reference. |
| T12 | image generation | Generated image/tool result is captured when the tool actually runs. | Assistant message has `image_generation` or image asset metadata. | Dashboard renders the generated image/card. |
| T13 | code interpreter | Tool call/output or interpreter artifact is captured. | Tool call/result/code output metadata is stored. | Dashboard renders tool/code output cards. |
| T14 | web search | Search/citation surface is captured when the tool actually runs. | Citations or search tool metadata are stored. | Dashboard renders citation/search cards. |
| T15 | canvas/artifact | Canvas/artifact content is captured. | Canvas/artifact block is stored in rich content. | Dashboard renders artifact/canvas card. |
| T16 | custom GPT/Gem | Account-scoped custom assistant surface is captured only when available. | Session metadata identifies the custom surface. | Render preserves custom assistant context/name. |
| T17 | project chat | Project-scoped chat is captured only when available. | Session metadata identifies project/workspace scope. | Render preserves project context. |
| T18 | temporary/private chat | Temporary chat behavior is captured without leaking into durable storage when site marks it private. | Storage either omits it or marks it temporary/private. | Render makes temporary/private status explicit if stored. |
| T19 | error state | Provider error/refusal/quota UI is captured. | Error state is stored as an error/tool/status record, not a fake assistant answer. | Dashboard renders the error state distinctly. |
| T20 | settings silent | Non-chat settings/account surfaces do not emit chat captures. | No new session/message rows are created. | Dashboard remains unchanged except health/capture diagnostics. |

## Tomorrow Run

Use strict standards for the structured cases:

```powershell
$env:PCE_PROBE_SITE_SET='first5'
$env:PCE_PROBE_STRICT_STANDARD='1'
python -m pytest tests/e2e_probe/test_matrix.py
```

If Google AI Studio quota is still exhausted, record it as an external SKIP
and run the other four sites. Do not count quota SKIPs as product coverage.
