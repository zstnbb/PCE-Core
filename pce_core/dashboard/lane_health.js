// SPDX-License-Identifier: Apache-2.0
// PCE Dashboard — Lane Health view (P5.C.1 Meta-Pipeline, 2026-05-12).
//
// This view is the operator-facing surface of the health-as-data
// contract defined in `Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md`.
// It is independent of the existing pipeline-Health view (`health.js`):
// pipeline-Health renders internal ingest/normalize/persist health
// (per source_id), while this view renders the cross-lane × target
// adapter health (browser/desktop/cli/mcp × ChatGPT/Claude/...).
//
// Polls `/api/v1/health/matrix` every 30 s while visible. Stops polling
// when the user navigates elsewhere.

(() => {
  const POLL_INTERVAL_MS = 30_000;
  const MATRIX_URL = "/api/v1/health/matrix";

  const LANE_ORDER = ["browser", "desktop", "cli", "mcp"];
  const COLOR_DOT = {
    green:  "🟢",
    yellow: "🟡",
    red:    "🔴",
    grey:   "⚪",
  };
  const COLOR_HEX = {
    green:  "#22c55e",
    yellow: "#eab308",
    red:    "#ef4444",
    grey:   "#94a3b8",
  };

  let pollTimer = null;
  let currentWindow = 24;

  document.addEventListener("DOMContentLoaded", () => {
    const navLink = document.querySelector('[data-view="lane-health"]');
    const section = document.getElementById("view-lane-health");
    const refreshBtn = document.getElementById("lane-health-refresh");
    const windowSel = document.getElementById("lane-health-window");

    if (!navLink || !section) {
      console.warn("[lane-health] required DOM nodes missing");
      return;
    }

    navLink.addEventListener("click", () => {
      refreshNow();
      startPolling();
    });

    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => refreshNow());
    }
    if (windowSel) {
      windowSel.addEventListener("change", () => {
        currentWindow = parseFloat(windowSel.value) || 24;
        refreshNow();
      });
    }

    const observer = new MutationObserver(() => {
      if (section.classList.contains("active")) {
        startPolling();
      } else {
        stopPolling();
      }
    });
    observer.observe(section, { attributes: true, attributeFilter: ["class"] });

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
      const resp = await fetch(
        `${MATRIX_URL}?window_hours=${currentWindow}`,
        { cache: "no-store" },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const matrix = await resp.json();
      render(matrix);
      updateTimestamp(matrix.computed_at, true);
    } catch (err) {
      console.warn("[lane-health] fetch failed", err);
      renderError(err);
      updateTimestamp(null, false);
    }
  }

  function render(matrix) {
    renderLaneRollup(matrix);
    renderTargets(matrix);
    renderRecent(matrix);
  }

  function renderLaneRollup(matrix) {
    const root = document.getElementById("lane-health-lanes");
    if (!root) return;
    const lanes = matrix.lanes || {};
    const cells = LANE_ORDER.map((lane) => {
      const laneData = lanes[lane] || { targets: {}, color: "grey" };
      const color = laneData.color || "grey";
      const targetCount = Object.keys(laneData.targets || {}).length;
      const dot = COLOR_DOT[color] || "⚪";
      return `
        <div class="lane-rollup-card" data-color="${escapeAttr(color)}">
          <div class="lane-rollup-header">
            <span class="lane-rollup-dot" style="color:${COLOR_HEX[color] || COLOR_HEX.grey}">${dot}</span>
            <span class="lane-rollup-name">${capitalize(lane)}</span>
          </div>
          <div class="lane-rollup-meta">
            ${targetCount} target${targetCount === 1 ? "" : "s"}
          </div>
        </div>
      `;
    }).join("");
    root.innerHTML = cells;
  }

  function renderTargets(matrix) {
    const root = document.getElementById("lane-health-targets");
    if (!root) return;
    const lanes = matrix.lanes || {};
    const rows = [];
    for (const lane of LANE_ORDER) {
      const laneData = lanes[lane] || { targets: {} };
      const targets = laneData.targets || {};
      for (const [targetId, t] of Object.entries(targets)) {
        rows.push({ lane, targetId, ...t });
      }
    }
    if (rows.length === 0) {
      root.innerHTML = `<p class="empty-state">No beacons in the last ${currentWindow} hours. Run a probe or trigger a capture to populate.</p>`;
      return;
    }
    rows.sort((a, b) => {
      // Most severe first, then alphabetical by lane+target.
      const sev = severityIndex(b.color) - severityIndex(a.color);
      if (sev !== 0) return sev;
      const k = `${a.lane}/${a.targetId}`;
      const m = `${b.lane}/${b.targetId}`;
      return k < m ? -1 : k > m ? 1 : 0;
    });
    root.innerHTML = `
      <table class="lane-health-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Lane</th>
            <th>Target</th>
            <th>Tier</th>
            <th>Planes</th>
            <th>Pass-rate</th>
            <th>Counts (pass / fail / skip / degraded)</th>
            <th>Last PASS</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => renderTargetRow(r)).join("")}
        </tbody>
      </table>
    `;
  }

  function renderTargetRow(r) {
    const color = r.color || "grey";
    const tier = r.tier || "—";
    const planes = `${r.plane_count ?? 0} / ${r.plane_required ?? 1}`;
    const planeOk = (r.plane_count ?? 0) >= (r.plane_required ?? 1);
    const passRate = (r.pass_rate_24h != null)
      ? `${(r.pass_rate_24h * 100).toFixed(1)}%`
      : "—";
    const counts = [
      r.pass_count_24h || 0,
      r.fail_count_24h || 0,
      r.skip_count_24h || 0,
      r.degraded_count_24h || 0,
    ].join(" / ");
    const lastPass = r.last_pass_ts ? formatRelative(r.last_pass_ts) : "never";
    return `
      <tr data-color="${escapeAttr(color)}">
        <td><span class="lane-health-dot" style="color:${COLOR_HEX[color] || COLOR_HEX.grey}">${COLOR_DOT[color] || "⚪"}</span></td>
        <td>${capitalize(r.lane)}</td>
        <td><code>${escape(r.targetId)}</code></td>
        <td>${escape(tier)}</td>
        <td class="${planeOk ? "" : "lane-health-bad"}">${planes}</td>
        <td>${passRate}</td>
        <td><code>${counts}</code></td>
        <td>${lastPass}</td>
      </tr>
    `;
  }

  function renderRecent(matrix) {
    const root = document.getElementById("lane-health-recent");
    if (!root) return;
    // Flatten the case_breakdown of every target into a single list,
    // sorted by ts DESC, take top 20. This avoids a second API round-trip
    // — the matrix already carries the latest per-case status.
    const items = [];
    const lanes = matrix.lanes || {};
    for (const [lane, laneData] of Object.entries(lanes)) {
      for (const [targetId, t] of Object.entries(laneData.targets || {})) {
        const breakdown = t.case_breakdown || {};
        for (const [caseId, c] of Object.entries(breakdown)) {
          items.push({
            ts: c.ts,
            status: c.status,
            lane,
            targetId,
            caseId,
          });
        }
      }
    }
    items.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    const top = items.slice(0, 20);
    if (top.length === 0) {
      root.innerHTML = `<p class="empty-state">No case-bound beacons yet.</p>`;
      return;
    }
    root.innerHTML = `
      <table class="lane-health-table">
        <thead>
          <tr><th>When</th><th>Status</th><th>Lane</th><th>Target</th><th>Case</th></tr>
        </thead>
        <tbody>
          ${top.map((it) => `
            <tr data-status="${escapeAttr(it.status)}">
              <td>${formatRelative(it.ts)}</td>
              <td><code>${escape(it.status)}</code></td>
              <td>${capitalize(it.lane)}</td>
              <td><code>${escape(it.targetId)}</code></td>
              <td><code>${escape(it.caseId)}</code></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  }

  function renderError(err) {
    const root = document.getElementById("lane-health-targets");
    if (!root) return;
    root.innerHTML = `<p class="error-state">Failed to load /api/v1/health/matrix: ${escape(String(err))}. Is PCE Core running on port 9800?</p>`;
  }

  function updateTimestamp(ts, ok) {
    const el = document.getElementById("lane-health-updated");
    if (!el) return;
    if (!ts) {
      el.textContent = ok ? "" : "(stale)";
      el.classList.toggle("ok", ok);
      el.classList.toggle("err", !ok);
      return;
    }
    const d = new Date(ts * 1000);
    el.textContent = `Updated ${d.toLocaleTimeString()}`;
    el.classList.toggle("ok", ok);
    el.classList.toggle("err", !ok);
  }

  // ---- helpers ----

  function severityIndex(color) {
    return { green: 0, grey: 1, yellow: 2, red: 3 }[color] ?? 1;
  }

  function capitalize(s) {
    if (!s) return "";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function formatRelative(ts) {
    if (!ts) return "—";
    const deltaS = (Date.now() / 1000) - ts;
    if (deltaS < 60) return `${Math.floor(deltaS)}s ago`;
    if (deltaS < 3600) return `${Math.floor(deltaS / 60)}m ago`;
    if (deltaS < 86400) return `${Math.floor(deltaS / 3600)}h ago`;
    return `${Math.floor(deltaS / 86400)}d ago`;
  }

  function escape(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[c]);
  }

  function escapeAttr(s) {
    return escape(s);
  }
})();
