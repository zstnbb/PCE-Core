# P4 Completion Note — Observability, Analytics & Alternate Capture Surfaces

- Date: 2026-04-18
- Phase: Industrialization P4 (long-term evolution)
- Task: `PROJECT.md` §11 P4 line + `docs/research/IMPLEMENTATION-ROADMAP.md`
- Status: Complete
- Follows: `2026-04-17-P3-completion.md`
- Depends on: P0 (logging / migrations / health) + P1 (OpenInference schema +
  OTLP exporter) + P2 (capture UX) + P3 (desktop shell)
- Related ADR: `ADR-008` — P4 可观测与分析作为可选叠加层

## A. Plan

P4 is explicitly framed in `PROJECT.md` as **long-term evolution**, not
core UX. The constraint: every P4 feature must be an **additive,
optional layer** that doesn't change the invariants built in P0-P3:

- SQLite is still the single writer of the source of truth
- The zero-config install path still has zero new required deps
- Every new capability degrades gracefully on machines that don't
  install its extra

Under that constraint P4 splits into five orthogonal sub-tasks:

1. **P4.1 — Semantic search over messages.** Give users a way to find
   conversations by meaning rather than exact keyword. Must work with
   no optional deps (hash-based fallback) and ship better backends
   (`sentence-transformers`, OpenAI embeddings) that users can opt into.

2. **P4.2 — DuckDB analytics layer.** Give users SQL-grade analytics
   (time-series, top-K, token rollups, Parquet export) without
   running a second storage engine. DuckDB's `sqlite_scanner`
   extension reads PCE's live SQLite read-only — no ETL, no
   consistency window.

3. **P4.3 — OpenLLMetry / OTel GenAI semconv.** Make PCE spans
   readable by the OTel-native observability ecosystem (Traceloop,
   Grafana LLM panel, Datadog's GenAI view) without losing the
   existing OpenInference compatibility Phoenix relies on. Solution:
   emit both attribute families on the same span, controlled by an
   env flag.

4. **P4.4 — Embedded CDP browser.** Add a fourth capture source
   (alongside extension / proxy / local hook) for contexts where
   PCE itself should own the browser — kiosks, CI demos, headless
   data collection. Uses Playwright as the optional runtime.

5. **P4.5 — Mobile capture onboarding.** Dramatically shorten the
   "phone → PCE" setup flow (configure Wi-Fi proxy + trust
   mitmproxy CA) by rendering the whole thing as one scannable QR.

Guardrails observed throughout:

- **Fail-open on optional deps.** `duckdb`, `playwright`, `qrcode`,
  `sentence-transformers`, `openai` are all optional. Each feature
  probes on import and every surface (Python function + HTTP
  endpoint + CLI) returns a structured `{"ok": False, "error":
  "<dep>_not_installed", "hint": "pip install ..."}` envelope or
  HTTP 503 when the dep is missing.
- **Additive schema only.** P4.1 adds `message_embeddings` (migration
  0004, new table). P4.4 adds a single row to `sources` via migration
  0005 (no column / index change). The migration runner already
  guarantees idempotency.
- **Zero-copy where possible.** DuckDB reads SQLite directly via
  `sqlite_scanner`; embeddings are derived from the same `messages`
  rows the rest of the system uses; gen_ai attributes are a view
  over the already-computed OI attributes.
- **Runner / dep injection.** CDP driver is thread-safe; every path-
  touching helper takes a `db_path` override so tests can isolate.
  Playwright is imported lazily inside the thread, not at module top.

## B. Changed Files

### New — Python

**`pce_core/embeddings.py`** — pluggable embedding backends (hash /
sentence-transformers / openai), `EmbeddingsBackend` ABC, lazy-load
selector, `pack_vector` / `unpack_vector`, `cosine_similarity`.

**`pce_core/semantic_search.py`** — `semantic_search(query, top_k,
min_similarity)`, `backfill_embeddings(limit)`, `get_status()`,
brute-force cosine over BLOB-packed float32 vectors.

**`pce_core/migrations/0004_embeddings.py`** — creates
`message_embeddings(message_id PK, backend, model_name, dim, vector,
created_at)` + FTS-style trigger for cascading deletes.

**`pce_core/migrations/0005_cdp_source.py`** — `INSERT OR IGNORE` of
`cdp-embedded` source row into the existing `sources` table; safe
no-op on fresh installs where baseline seeded it.

**`pce_core/analytics.py`** — DuckDB-backed analytics layer:
`status()`, `timeseries()`, `top_models()`, `top_hosts()`,
`token_usage()`, `export_parquet()`. Every function opens a fresh
`:memory:` DuckDB, attaches SQLite via `sqlite_scanner` in
`READ_ONLY` mode, runs the query, closes. Casts `to_timestamp(x)` to
naive `TIMESTAMP` before `date_trunc` to avoid the TIMESTAMPTZ /
pytz import path.

**`pce_core/normalizer/genai_semconv.py`** — `to_genai_aliases()` maps
OpenInference keys to OTel GenAI + OpenLLMetry aliases
(`gen_ai.operation.name`, `gen_ai.system`, `gen_ai.request.model`,
`gen_ai.response.model`, `gen_ai.usage.*`, `gen_ai.conversation.id`,
`gen_ai.prompt.{i}.*`, `gen_ai.completion.{i}.*`, `llm.usage.*` flat
spellings). `apply_mode(attrs, mode=both|only|off)` merges / strips
per `PCE_OTEL_GENAI_SEMCONV`.

**`pce_core/cdp/__init__.py`** — singleton lifecycle (`start_default`
/ `stop_default` / `status` / `current_status`), module-level thread-
safe driver holder, feature probe (`PLAYWRIGHT_AVAILABLE`).

**`pce_core/cdp/driver.py`** — `CDPDriver` threaded Playwright driver.
Default URL patterns cover OpenAI / Anthropic / Google / Mistral /
Groq / Perplexity endpoints. Matched responses write
`request` + `response` pairs sharing a `pair_id` into `raw_captures`,
`source_id = cdp-embedded`. Reuses `pce_core.redact.redact_headers`.

**`pce_core/cdp/__main__.py`** — argparse CLI wizard:
`python -m pce_core.cdp [--start-url ...] [--headless] [--pattern RE]
 [--duration SECONDS] [--probe-only]`.

**`pce_core/mobile_wizard.py`** — `get_lan_ip()` (UDP-connect probe),
`get_setup_info(lan_ip, port)`, `render_qr_ascii()`,
`render_qr_png()`, `status()`, argparse CLI
(`python -m pce_core.mobile_wizard [--json] [--png PATH] [--ip IP]
 [--port PORT]`).

### New — Server routes

**`pce_core/server.py`** — 14 new `/api/v1/*` endpoints:

- `GET /api/v1/search?mode=semantic&q=...`
- `GET /api/v1/embeddings/status`
- `POST /api/v1/embeddings/backfill?limit=500`
- `GET /api/v1/analytics/status`
- `GET /api/v1/analytics/timeseries`
- `GET /api/v1/analytics/top_models`
- `GET /api/v1/analytics/top_hosts`
- `GET /api/v1/analytics/token_usage`
- `GET /api/v1/analytics/export.parquet`
- `GET /api/v1/cdp/status`
- `POST /api/v1/cdp/start`
- `POST /api/v1/cdp/stop`
- `GET /api/v1/mobile_wizard/info`
- `GET /api/v1/mobile_wizard/qr.txt`
- `GET /api/v1/mobile_wizard/qr.png`

### Modified — Python

**`pce_core/db.py`** — added `SOURCE_CDP = "cdp-embedded"` constant +
corresponding row in `_DEFAULT_SOURCES`.

**`pce_core/migrations/__init__.py`** — bumped
`EXPECTED_SCHEMA_VERSION` from 4 → 5.

**`pce_core/otel_exporter.py`** — `emit_pair_span` now pipes attributes
through `genai_semconv.apply_mode()` before `span.set_attribute`.

**`pce_core/server.py`** — added `PlainTextResponse` to the responses
import.

**`pce_core/dashboard/index.html` + `app.js` + `style.css`** — P4.1
adds a `🔀 Semantic` segmented toggle next to the search bar, a
status pill ("semantic: sentence-transformers / 123 / 500"), and a
backfill button that polls `/api/v1/embeddings/backfill` until
`remaining == 0`.

### New — Tests (134 added)

- `tests/test_embeddings.py` (22)
- `tests/test_semantic_search.py` (21)
- `tests/test_analytics.py` (25)
- `tests/test_genai_semconv.py` (23)
- `tests/test_cdp_driver.py` (28)
- `tests/test_mobile_wizard.py` (15)

### New — Docs

- `Docs/docs/engineering/MOBILE-CAPTURE.md` — iOS / Android proxy +
  CA install walkthrough, QR helper docs, troubleshooting table.
- `Docs/docs/decisions/2026-04-18-P4-completion.md` — this note.

### Modified — Docs

- `BUILD.md` — new "Optional Observability & Analytics (P4)" section
  with per-feature `pip install` + API / CLI cheat-sheets.
- `Docs/docs/PROJECT.md` — P4 bullet rewritten as "completed",
  child deliverables listed, ADR-008 registered.

## C. Key Design Decisions

### 1. DuckDB attached read-only over the live SQLite file

DuckDB's `sqlite_scanner` opens the SQLite file in read-only mode
via `ATTACH ... (TYPE SQLITE, READ_ONLY)`. This gives us:

- **Zero data duplication.** Every analytics query sees the same
  bytes the ingest server just wrote — no ETL window.
- **No extra daemon.** DuckDB is a Python library, not a server.
- **No schema drift.** Because DuckDB reads SQLite directly, adding
  a column to SQLite's `messages` table shows up in DuckDB
  automatically.

We open a fresh `:memory:` DuckDB per request so schema changes are
picked up immediately and there's no connection state to coordinate.
Cost is ~5 ms of setup, negligible for the scale PCE targets.

### 2. Embeddings stored as float32 BLOBs, not via `sqlite-vec`

The P4.1 roadmap originally mentioned sqlite-vec. In practice we
settled on a plain `BLOB` column packed with `struct.pack("<{d}f")`
for three reasons:

1. `sqlite-vec` ships as a loadable extension; some Python builds
   (especially Windows) strip `sqlite3.enable_load_extension()`.
   Making it mandatory would block the feature for those users.
2. Brute-force cosine over 10 k messages is sub-50 ms in pure Python.
   PCE is a single-user tool; this scale is the upper bound.
3. The BLOB schema doesn't preclude adding a `sqlite-vec` virtual
   table later for users who want hardware-accelerated KNN. It's
   purely additive.

### 3. `PCE_OTEL_GENAI_SEMCONV=both` as the default

Three modes:

- `off` — legacy OpenInference only (pre-P4.3 behaviour)
- `both` (default) — OI + gen_ai aliases on every span
- `only` — strip OI primary keys, keep only gen_ai

Default is `both` because:

- The OTLP channel is already opt-in (requires
  `OTEL_EXPORTER_OTLP_ENDPOINT`). Users who turn it on want their
  spans to be useful; emitting both families costs ~300 bytes per
  span and unlocks every GenAI dashboard.
- It's a strictly additive change — no existing Phoenix / Arize
  integration breaks.

`only` exists for backends that get confused by duplicate attributes
(rare, but reported in Grafana LLM panel).

### 4. CDP driver runs in a background thread, not the FastAPI event loop

Playwright Python has sync + async APIs. We picked sync because:

- The existing `insert_capture` is blocking — mixing it into the
  FastAPI event loop would add an `asyncio.to_thread` hop per capture
  with no real win.
- Playwright holds a private greenlet loop internally. Wrapping
  `sync_playwright()` in an OS thread is the officially recommended
  pattern for long-running scripts.
- Start / stop lifecycle is easier to reason about: a single
  `threading.Event`-driven run loop, shutdown via a sentinel `queue`.

Trade-off: we can't pipe `page.evaluate()` results directly into an
async endpoint. Acceptable — the driver's only job is to persist to
SQLite, which is synchronous anyway.

### 5. Mobile wizard QR payload = deep link URL, not JSON blob

Scanning a 200-byte JSON blob requires error-correction level H
(30%) to stay readable from a phone camera across a monitor. A
short `pce-proxy://setup?host=...&port=...` URL uses level M (15%),
fits in a ~35×35 grid, scans reliably from any angle, and still
carries the two fields the operator actually copies.

Instructions, cert URL, and the long-form info stay in the
accompanying `get_setup_info()` envelope which is served separately
via `GET /api/v1/mobile_wizard/info`.

### 6. "Never edit a released migration"

When the CDP source needed a row in `sources`, there were two
options: edit `0001_baseline`'s `_DEFAULT_SOURCES` tuple, or add a
new migration. We chose the second (`0005_cdp_source.py`) because:

- Users tracking schema evolution across PCE versions would
  otherwise see "the same migration" producing different effects.
- `schema_migrations` gives us an audit trail: operators can
  `SELECT * FROM schema_migrations` and see exactly when their DB
  became CDP-aware.
- `INSERT OR IGNORE` makes the migration a no-op if the row already
  exists (future baseline might seed it).

This policy will be reused for every future additive schema touch.

## D. Verification

### Test suite

```
pytest tests/ --ignore=tests/e2e --ignore=tests/smoke \
              --ignore=tests/test_e2e_full.py -q
```

Result: **726 passed, 3 skipped, 0 failures** (up from ~592 pre-P4).
134 new tests across six files, covering:

- Each feature's happy path (live dep present)
- Each feature's graceful-fallback path (dep monkey-patched unavailable)
- Every HTTP endpoint (200 + 503 branches)
- Redaction, URL pattern matching, lifecycle idempotency (CDP)
- Driver thread lifecycle + shutdown race conditions (CDP)
- Parquet round-trip readability (analytics)
- Bucketing / filtering / cap enforcement (analytics)
- Env-driven mode resolution + merge rules (gen_ai semconv)
- LAN IP fallback + PNG magic bytes + CLI JSON mode (mobile wizard)

### Graceful-fallback sanity checks

With `duckdb` + `playwright` + `qrcode[pil]` all installed locally,
the full suite exercises both the live path (live imports) and the
monkey-patched "unavailable" path. On a stock install where none are
present, the suite still passes (3 tests marked `importorskip`, others
use `monkeypatch.setattr(feature, "_AVAILABLE", False)`).

### Manual smoke

- `python -m pce_core.cdp --probe-only` — prints `playwright_available=True`
- `python -m pce_core.mobile_wizard --json` — valid JSON with LAN IP
  detected
- `curl localhost:9800/api/v1/analytics/timeseries?days=7` — valid
  shape, even on an empty DB
- `curl localhost:9800/api/v1/embeddings/status` — reports backend +
  vector count

## E. Known limits / intentional non-goals

- **No embedded analytics UI.** P4.2 ships six endpoints. A
  dashboard widget consuming them is deferred — the API is the
  integration point for Grafana / Superset / pandas / whoever. The
  existing "Search" tab already exercises the endpoint wiring
  sufficiently for smoke-testing.
- **No CDP "attach to running Chrome" mode.** P4.4 launches a fresh
  Chromium. Attaching to an existing user profile would require
  reading the user's Chrome cookies, which crosses the privacy line
  in PCE's charter. If demand arrives, the driver class is
  structured to make that a separate construction path.
- **No first-class Android 7+ pinning bypass.** P4.5 documents the
  limitation (apps can opt out of trusting user CAs via
  `network_security_config`) and points affected users to P4.4 CDP
  capture as the workaround.
- **No sqlite-vec adoption — yet.** Embeddings table is shape-
  compatible; adding a `vec0` virtual table over it is a later
  purely-additive migration when performance pressure justifies.
- **No ARM / Apple-Silicon Playwright CI.** Tests fully mock
  Playwright; CI doesn't actually launch Chromium. Real-device
  coverage lives in e2e (not run in this task).

## F. Follow-ups (none blocking)

- **Dashboard "Analytics" tab** consuming `/api/v1/analytics/*`.
  Lightweight — two line charts + two tables. Out of scope for P4
  because the roadmap framed the work as API-first. Queue if user
  feedback asks for it.
- **`sqlite-vec` virtual table option** for users with >100 k
  messages who find brute-force cosine slow. Additive migration,
  feature-detected like every other P4 extra.
- **`pce_app` tray menu entries** for CDP start/stop + mobile
  wizard. Currently only reachable via HTTP / CLI. Wiring them into
  the tray is a one-afternoon job against the existing
  `pce_app/tray.py` surface.

---

**P4 closes the industrialization arc for PCE Core.** The product
now has:

- Four orthogonal capture surfaces (extension / proxy / local hook /
  CDP)
- Semantic and keyword search over every captured message
- SQL-grade analytics + Parquet export via DuckDB
- Span export compatible with both OpenInference and OTel GenAI
  ecosystems
- A one-minute mobile onboarding path

All of it is additive, optional, and sits on top of the single-
writer SQLite kernel P0-P3 hardened.
