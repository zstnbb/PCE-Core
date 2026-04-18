// SPDX-License-Identifier: Apache-2.0
// PCE Dashboard — Health view (added in P0, 2026-04-17).
//
// Self-contained: does not touch app.js beyond listening for the existing
// nav click pattern. Polls /api/v1/health every 10 s while the health
// view is visible and stops when the user navigates elsewhere.

(() => {
  const POLL_INTERVAL_MS = 10_000;
  const HEALTH_URL = "/api/v1/health";
  const MIG_URL = "/api/v1/health/migrations";

  const STAGE_LABELS = {
    ingest: "Ingest",
    normalize: "Normalize",
    reconcile: "Reconcile",
    session_resolve: "Session resolve",
    persist: "Persist",
    fts_index: "FTS index",
    otel_emit: "OTLP emit",
    tag: "Auto-tag",
  };
  const ALL_STAGES = [
    "ingest", "normalize", "reconcile", "session_resolve",
    "persist", "fts_index", "otel_emit", "tag",
  ];

  const SOURCE_LABELS = {
    "proxy-default": "HTTPS Proxy",
    "browser-extension-default": "Browser Extension",
    "mcp-default": "MCP Server",
  };

  let pollTimer = null;
  let lastFetchOk = false;

  document.addEventListener("DOMContentLoaded", () => {
    const navLink = document.querySelector('[data-view="health"]');
    const section = document.getElementById("view-health");
    const refreshBtn = document.getElementById("health-refresh");

    if (!navLink || !section) {
      console.warn("[health] required DOM nodes missing");
      return;
    }

    // Fire an immediate refresh and start polling when the user opens the
    // health view, and stop polling when they leave.
    navLink.addEventListener("click", () => {
      refreshNow();
      startPolling();
    });

    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        refreshNow();
      });
    }

    // Watch for the 'active' class toggling elsewhere (e.g. via switchView)
    // so we turn polling on/off in lockstep.
    const observer = new MutationObserver(() => {
      if (section.classList.contains("active")) {
        startPolling();
      } else {
        stopPolling();
      }
    });
    observer.observe(section, { attributes: true, attributeFilter: ["class"] });

    // If the page loaded directly on /dashboard/#health-like anchors the
    // view may already be active.
    if (section.classList.contains("active")) {
      refreshNow();
      startPolling();
    }
  });

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(refreshNow, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function refreshNow() {
    try {
      const [healthResp, migResp] = await Promise.all([
        fetch(HEALTH_URL, { cache: "no-store" }),
        fetch(MIG_URL, { cache: "no-store" }),
      ]);
      if (!healthResp.ok) throw new Error(`health HTTP ${healthResp.status}`);
      const health = await healthResp.json();
      const migrations = migResp.ok ? await migResp.json() : { history: [] };
      lastFetchOk = true;
      render(health, migrations);
      updateTimestamp(health.timestamp);
    } catch (err) {
      lastFetchOk = false;
      renderError(err);
    }
  }

  function updateTimestamp(tsSeconds) {
    const el = document.getElementById("health-last-updated");
    if (!el) return;
    if (!tsSeconds) {
      el.textContent = "";
      return;
    }
    const d = new Date(tsSeconds * 1000);
    el.textContent = `Updated ${d.toLocaleTimeString()}`;
    el.classList.toggle("ok", lastFetchOk);
    el.classList.toggle("err", !lastFetchOk);
  }

  function renderError(err) {
    const raw = document.getElementById("health-raw");
    if (raw) {
      raw.textContent = `Failed to load health snapshot:\n${err && err.message ? err.message : err}`;
    }
    const updated = document.getElementById("health-last-updated");
    if (updated) {
      updated.textContent = "Offline";
      updated.classList.remove("ok");
      updated.classList.add("err");
    }
  }

  // --------------------------------------------------------------------
  // Rendering
  // --------------------------------------------------------------------

  function render(health, migrations) {
    renderSchemaRow(health, migrations);
    renderSources(health.sources || {});
    renderPipeline(health.pipeline || {});
    renderStorage(health.storage || {});
    renderRaw(health, migrations);
  }

  function renderSchemaRow(health, migrations) {
    const el = document.getElementById("health-schema-row");
    if (!el) return;
    const schema = health.schema_version;
    const expected = health.expected_schema_version;
    const payload = health.capture_payload_version;
    const migOk = migrations && migrations.ok !== false && schema === expected;
    const historyCount = (migrations && migrations.history) ? migrations.history.length : 0;
    const latencyAgeSec = health.timestamp ? Math.round(Date.now() / 1000 - health.timestamp) : null;

    el.innerHTML = `
      <div class="health-schema-card ${migOk ? 'ok' : 'warn'}">
        <div class="health-schema-key">Schema version</div>
        <div class="health-schema-val">${escape(schema)} / expected ${escape(expected)}</div>
        <div class="health-schema-sub">${historyCount} migration(s) applied</div>
      </div>
      <div class="health-schema-card">
        <div class="health-schema-key">Capture payload</div>
        <div class="health-schema-val">v${escape(payload)}</div>
        <div class="health-schema-sub">raw_captures row format</div>
      </div>
      <div class="health-schema-card">
        <div class="health-schema-key">Server</div>
        <div class="health-schema-val">v${escape(health.version || "?")}</div>
        <div class="health-schema-sub mono">${escape(health.ingest_host)}:${escape(health.ingest_port)}</div>
      </div>
      <div class="health-schema-card">
        <div class="health-schema-key">Snapshot age</div>
        <div class="health-schema-val">${latencyAgeSec == null ? "–" : `${latencyAgeSec}s`}</div>
        <div class="health-schema-sub">server clock</div>
      </div>
    `;
  }

  function renderSources(sources) {
    const el = document.getElementById("health-sources-grid");
    if (!el) return;
    const entries = Object.entries(sources);
    if (!entries.length) {
      el.innerHTML = `<div class="health-empty">No sources recorded yet.</div>`;
      return;
    }
    entries.sort((a, b) => sourceOrder(a[0]) - sourceOrder(b[0]));
    el.innerHTML = entries.map(([id, info]) => renderSourceCard(id, info)).join("");
  }

  function sourceOrder(id) {
    const order = ["proxy-default", "browser-extension-default", "mcp-default"];
    const i = order.indexOf(id);
    return i === -1 ? 100 : i;
  }

  function renderSourceCard(id, info) {
    const label = SOURCE_LABELS[id] || id;
    const status = sourceStatus(info);
    const lastSeen = info.last_capture_ago_s == null
      ? "never"
      : `${formatAge(info.last_capture_ago_s)} ago`;

    return `
      <div class="health-source-card status-${status.cls}">
        <div class="health-source-header">
          <span class="health-source-dot status-${status.cls}"></span>
          <div class="health-source-title">${escape(label)}</div>
          <span class="health-source-badge status-${status.cls}">${status.label}</span>
        </div>
        <div class="health-source-body">
          <div class="health-metric"><span>Last capture</span><b>${lastSeen}</b></div>
          <div class="health-metric"><span>1h</span><b>${escape(info.captures_last_1h)}</b></div>
          <div class="health-metric"><span>24h</span><b>${escape(info.captures_last_24h)}</b></div>
          <div class="health-metric"><span>Total</span><b>${escape(info.captures_total)}</b></div>
          <div class="health-metric"><span>Failures 24h</span><b class="${info.failures_last_24h ? 'warn' : ''}">${escape(info.failures_last_24h)}</b></div>
          <div class="health-metric"><span>Drop rate</span><b class="${info.drop_rate > 0.05 ? 'warn' : ''}">${formatRate(info.drop_rate)}</b></div>
          <div class="health-metric"><span>p50</span><b>${formatMs(info.latency_p50_ms)}</b></div>
          <div class="health-metric"><span>p95</span><b>${formatMs(info.latency_p95_ms)}</b></div>
        </div>
        <div class="health-source-footer mono">${escape(id)}</div>
      </div>
    `;
  }

  function sourceStatus(info) {
    if (info.last_capture_ago_s == null) {
      if (info.captures_total === 0) return { cls: "never", label: "Never" };
      return { cls: "stale", label: "Stale" };
    }
    if (info.last_capture_ago_s < 300) return { cls: "active", label: "Active" };
    if (info.last_capture_ago_s < 3600) return { cls: "idle", label: "Idle" };
    if (info.last_capture_ago_s < 86400) return { cls: "stale", label: "Stale" };
    return { cls: "inactive", label: "Inactive" };
  }

  function renderPipeline(pipeline) {
    const el = document.getElementById("health-pipeline");
    if (!el) return;
    const byStage = pipeline.errors_last_24h_by_stage || {};
    const rows = ALL_STAGES.map((stage) => {
      const bucket = byStage[stage] || {};
      const err = Number(bucket.ERROR || 0);
      const warn = Number(bucket.WARNING || 0);
      const total = err + warn;
      const cls = err > 0 ? "err" : warn > 0 ? "warn" : "ok";
      return `
        <div class="health-stage-row status-${cls}">
          <div class="health-stage-name">${escape(STAGE_LABELS[stage] || stage)}</div>
          <div class="health-stage-counts">
            <span class="count err">${err} err</span>
            <span class="count warn">${warn} warn</span>
            <span class="count total">${total} total</span>
          </div>
        </div>
      `;
    }).join("");

    const ftsLag = pipeline.fts_index_lag_rows;
    const ftsCls = ftsLag == null ? "ok" : ftsLag === 0 ? "ok" : ftsLag < 10 ? "warn" : "err";

    el.innerHTML = `
      <div class="health-stage-list">${rows}</div>
      <div class="health-fts-lag status-${ftsCls}">
        <span>FTS index lag</span>
        <b>${ftsLag == null ? "–" : `${ftsLag} rows`}</b>
        <small>(messages.rowid − messages_fts.rowid)</small>
      </div>
    `;
  }

  function renderStorage(storage) {
    const el = document.getElementById("health-storage");
    if (!el) return;
    el.innerHTML = `
      <div class="health-storage-grid">
        <div class="health-storage-card">
          <div class="health-storage-key">DB size</div>
          <div class="health-storage-val">${formatBytes(storage.db_size_bytes)}</div>
          <div class="health-storage-sub mono">${escape(storage.db_path || "-")}</div>
        </div>
        <div class="health-storage-card">
          <div class="health-storage-key">Raw captures</div>
          <div class="health-storage-val">${escape(storage.raw_captures_rows)}</div>
        </div>
        <div class="health-storage-card">
          <div class="health-storage-key">Sessions</div>
          <div class="health-storage-val">${escape(storage.sessions_rows)}</div>
        </div>
        <div class="health-storage-card">
          <div class="health-storage-key">Messages</div>
          <div class="health-storage-val">${escape(storage.messages_rows)}</div>
        </div>
        <div class="health-storage-card">
          <div class="health-storage-key">Snippets</div>
          <div class="health-storage-val">${escape(storage.snippets_rows)}</div>
        </div>
        <div class="health-storage-card">
          <div class="health-storage-key">Oldest capture</div>
          <div class="health-storage-val">${storage.oldest_capture_age_days == null ? "–" : `${storage.oldest_capture_age_days} d`}</div>
        </div>
      </div>
    `;
  }

  function renderRaw(health, migrations) {
    const el = document.getElementById("health-raw");
    if (!el) return;
    const payload = { health, migrations };
    try {
      el.textContent = JSON.stringify(payload, null, 2);
    } catch (e) {
      el.textContent = String(payload);
    }
  }

  // --------------------------------------------------------------------
  // Formatting helpers
  // --------------------------------------------------------------------

  function formatAge(seconds) {
    if (seconds == null) return "–";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m${s % 60 ? ` ${s % 60}s` : ""}`;
    if (s < 86400) return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60) ? ` ${Math.floor((s % 3600) / 60)}m` : ""}`;
    return `${Math.floor(s / 86400)}d`;
  }

  function formatMs(v) {
    if (v == null) return "–";
    if (v < 1000) return `${Math.round(v)}ms`;
    return `${(v / 1000).toFixed(2)}s`;
  }

  function formatRate(v) {
    if (v == null) return "–";
    if (v === 0) return "0%";
    return `${(v * 100).toFixed(2)}%`;
  }

  function formatBytes(bytes) {
    if (bytes == null) return "–";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  function escape(v) {
    if (v == null) return "–";
    const s = String(v);
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
