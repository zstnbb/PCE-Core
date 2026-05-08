# ChatGPT + Claude T10 / T11 — User-upload attachment extractor gap

**Filed:** 2026-04-30 (probe-framework vertical run)
**Confirmed cross-site:** 2026-04-30 — Claude reproduces the same shape (probe vertical 10 PASS / 9 SKIP / 2 FAIL on T10+T11)
**Reproduces:** every run, deterministic
**Severity:** medium — pipeline captures the conversation, but loses file/image metadata on the user message
**Owner:** SW extractor (`pce_browser_extension_wxt/entrypoints/<chatgpt|claude content script>`, `pce_dom_utils.js`, `service_worker.js`, `pce_core/conversation.py`)

---

## Symptom

Two probe cases reproduce the bug:

- **T10** — user uploads a small PDF via the file input, sends a prompt that includes a unique token and the literal filename. The token round-trips into PCE Core (so the conversation is captured), but the user message row has `content_json: None` — no `file` attachment metadata is persisted.
- **T11** — same shape, but with a small inline-generated PNG. The image chip renders in the ChatGPT UI, the prompt sends, the assistant replies, but the user message has no `image_url` attachment in `content_json`.

Both cases were verified by direct inspection of `/api/v1/sessions/<id>/messages`:

```
session 56dcf46b00d3 (T10)
  user msg: content_text="I just attached a small PDF named pce_probe_t10_…"
            content_json: None        <- the bug
  assistant msg: normal text reply
```

The case-side assertion lives in
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\cases\t10_pdf_upload.py`
and `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\cases\t11_image_upload.py`
(check for `attachments` array in the user message — empty/missing → fail).

## What is captured correctly

- The probe successfully injects the file via DataTransfer (the chip renders in the composer).
- The prompt sends and ChatGPT replies normally.
- The conversation **is** captured: a session row appears in PCE Core, both user and assistant text messages appear, and `session_key` matches the URL-extracted hint.
- Assistant attachments (e.g. citations on T14, code_block on T05) are extracted correctly on the same pipeline. The bug is **specifically** on user-uploaded files/images.

## What is missing

The `content_json.attachments` array on the **user** message row should contain at least one of:

- For T10 (PDF): `{"type": "file", "name": "pce_probe_t10_…pdf", "media_type": "application/pdf", ...}`
- For T11 (PNG): `{"type": "image_url", "url": "...", "media_type": "image/png", ...}`

Both are documented in the rich-content design (see memory: "Attachment Types Supported" table).

## Likely root cause (hypothesis, not verified)

ChatGPT's WS frame for a user message with attachments is `{"content_type": "text", "parts": [...]}` plus a separate `metadata` block listing `attachments` and `image_asset_pointer` entries. `_clean_content()` in `@f:\INVENTION\You.Inc\PCE Core\pce_core\conversation.py` knows how to extract `image_asset_pointer` for **inline-rendered** images (the existing path proven by manual ChatGPT runs — see memory: "_clean_content() in conversation.py detects ChatGPT WS protocol format and extracts actual text + image_asset_pointer attachments").

Two candidate paths to investigate:

1. **DataTransfer-injected uploads bypass the React upload pipeline.** The probe's
   `upload_file_via_input` (in `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\base.py`)
   fires a synthetic `change` event on the file input. React may verify
   `event.isTrusted === true` and skip the upload. If the file never gets
   uploaded server-side, the WS frame won't carry attachment metadata at all.
   Verify by checking: does the chip have a real `file_id` after upload, or
   is it a placeholder? Inspect the network tab for `POST /backend-api/files/...`.
2. **`_clean_content()` doesn't handle the user-side attachment shape.** Even
   if the upload succeeds, the WS frame for the user turn may use a different
   key (e.g. `attachments: [{name, mimeType, file_token}]`) that the cleaner
   doesn't recognize, so the metadata gets dropped during normalization.
   Verify by adding a service-worker `console.log` of the raw user-message
   WS frame and comparing it against `_clean_content()`'s parsing branches.

The first hypothesis is more likely given that hypothesis 2 would have been
caught by the manual-run validation that proved `image_asset_pointer`
extraction. Hypothesis 1 specifically affects the probe (synthetic event)
but not a real human upload.

## Reproduction

```powershell
# Pre: PCE Core running on 9800; Chrome attached with PCE extension
python -m pytest tests/e2e_probe/test_matrix.py `
    -k "chatgpt and (T10 or T11)" `
    -v --tb=line
```

Both cells fail with:

```
user message has no attachments captured. The token made it through
(extractor saw the user prompt) but the file chip's metadata didn't
survive normalization. […] for ChatGPT/GAS this is a real extractor bug.
```

## Suggested next steps

1. Run T10 once, then in a separate Chrome devtools window inspect the
   `/backend-api/conversation` WS frame for the user message. Save the raw
   JSON of `message.content` and `message.metadata.attachments`.
2. If the WS frame **does** contain attachment metadata: add the missing
   parser branch in `_clean_content()` and the corresponding mapping into
   `content_json.attachments`. Add a unit test in
   `@f:\INVENTION\You.Inc\PCE Core\tests\test_rich_content.py` with the
   captured frame as a fixture.
3. If the WS frame does **not** contain attachment metadata: the upload
   never fired. Compare against a real human upload in the same session;
   the fix is on the probe side (different upload technique — e.g. CDP
   `Input.dispatchDragEvent` instead of synthetic `change`). Track in
   `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\base.py` under
   `upload_file_via_input`.
4. Re-run the probe vertical and confirm T10 / T11 flip to ✅.

## Cross-site implications

This is filed as a ChatGPT-specific gap, but the same DataTransfer-injection
technique is used for Claude / Gemini / GAS / Grok. Once the root cause is
identified, the other four verticals should be re-examined — if hypothesis 1
holds, all five sites likely share the bug; if hypothesis 2 holds, each site
has its own attachment-frame shape and needs a per-extractor fix.
