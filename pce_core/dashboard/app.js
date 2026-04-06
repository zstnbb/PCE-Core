/**
 * PCE Dashboard – Single-page application
 *
 * Communicates with the FastAPI backend at /api/v1/*
 */

const API = "/api/v1";

// ── State ───────────────────────────────────────────────
let currentView = "stats";

// ── DOM refs ────────────────────────────────────────────
const views = document.querySelectorAll(".view");
const navLinks = document.querySelectorAll(".nav-links a");
const statusDot = document.getElementById("status-dot");
const serverStatus = document.getElementById("server-status");

// ── Navigation ──────────────────────────────────────────
navLinks.forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    const view = link.dataset.view;
    switchView(view);
  });
});

function switchView(name) {
  currentView = name;
  views.forEach((v) => v.classList.remove("active"));
  navLinks.forEach((a) => a.classList.remove("active"));

  document.getElementById(`view-${name}`).classList.add("active");
  document.querySelector(`[data-view="${name}"]`).classList.add("active");

  if (name === "stats") loadStats();
  if (name === "sessions") loadSessions();
  if (name === "captures") loadCaptures();
  if (name === "services") loadServices();
}

// ── API helpers ─────────────────────────────────────────
async function api(path) {
  const resp = await fetch(`${API}${path}`);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

function formatTime(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

function formatShortTime(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function prettyJson(str) {
  try {
    return JSON.stringify(JSON.parse(str), null, 2);
  } catch {
    return str;
  }
}

// ── Health check ────────────────────────────────────────
async function checkHealth() {
  try {
    const data = await api("/health");
    statusDot.classList.add("online");
    serverStatus.textContent = `${data.total_captures} captures`;
  } catch {
    statusDot.classList.remove("online");
    serverStatus.textContent = "Offline";
  }
}

// ── Stats View ──────────────────────────────────────────
async function loadStats() {
  try {
    const [stats, captures, sessions] = await Promise.all([
      api("/stats"),
      api("/captures?last=10"),
      api("/sessions?last=5"),
    ]);

    document.getElementById("stat-total").textContent = stats.total_captures;
    document.getElementById("stat-sessions").textContent = sessions.length;
    document.getElementById("stat-providers").textContent =
      Object.keys(stats.by_provider || {}).length;
    document.getElementById("stat-sources").textContent =
      Object.keys(stats.by_source || {}).length;

    renderBreakdown("by-provider", stats.by_provider);
    renderBreakdown("by-direction", stats.by_direction);
    renderBreakdown("by-source", stats.by_source);
    renderCaptureList("recent-captures", captures, 10);

    // Populate filter dropdowns
    populateProviderFilters(stats.by_provider);
  } catch (e) {
    console.error("Failed to load stats:", e);
  }
}

function renderBreakdown(containerId, data) {
  const el = document.getElementById(containerId);
  if (!data || Object.keys(data).length === 0) {
    el.innerHTML = '<div class="empty-state">No data</div>';
    return;
  }
  const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]);
  el.innerHTML = sorted
    .map(
      ([key, val]) =>
        `<div class="breakdown-item"><span class="key">${escapeHtml(key)}</span><span class="val">${val}</span></div>`
    )
    .join("");
}

function populateProviderFilters(byProvider) {
  const providers = Object.keys(byProvider || {});
  ["session-provider-filter", "capture-provider-filter"].forEach((id) => {
    const sel = document.getElementById(id);
    const current = sel.value;
    sel.innerHTML = '<option value="">All Providers</option>';
    providers.forEach((p) => {
      sel.innerHTML += `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`;
    });
    sel.value = current;
  });
}

// ── Captures View ───────────────────────────────────────
async function loadCaptures() {
  const provider = document.getElementById("capture-provider-filter").value;
  const direction = document.getElementById("capture-direction-filter").value;

  let url = "/captures?last=100";
  if (provider) url += `&provider=${encodeURIComponent(provider)}`;

  try {
    let captures = await api(url);
    if (direction) {
      captures = captures.filter((c) => c.direction === direction);
    }
    renderCaptureList("captures-list", captures, 100);
    document.getElementById("capture-detail").classList.add("hidden");
  } catch (e) {
    console.error("Failed to load captures:", e);
  }
}

function renderCaptureList(containerId, captures, limit) {
  const el = document.getElementById(containerId);
  if (!captures || captures.length === 0) {
    el.innerHTML = '<div class="empty-state">No captures yet. Start using AI tools to see data here.</div>';
    return;
  }

  el.innerHTML = captures
    .slice(0, limit)
    .map(
      (c) => `
      <div class="capture-row" data-pair-id="${escapeHtml(c.pair_id)}">
        <span class="capture-time">${formatTime(c.created_at)}</span>
        <span class="tag tag-${c.direction}">${c.direction}</span>
        <span class="tag tag-provider">${escapeHtml(c.provider)}</span>
        <span class="capture-host">${escapeHtml(c.host)}</span>
        <span class="capture-path">${escapeHtml(c.path)}</span>
        ${c.model_name ? `<span class="capture-model">${escapeHtml(c.model_name)}</span>` : ""}
        ${c.status_code ? `<span class="tag">${c.status_code}</span>` : ""}
      </div>
    `
    )
    .join("");

  // Click handlers
  el.querySelectorAll(".capture-row").forEach((row) => {
    row.addEventListener("click", () => {
      const pairId = row.dataset.pairId;
      loadCapturePair(pairId);
    });
  });
}

async function loadCapturePair(pairId) {
  try {
    const captures = await api(`/captures/pair/${pairId}`);
    const container = document.getElementById("capture-pair-content");
    document.getElementById("capture-pair-title").textContent = `Pair ${pairId.slice(0, 12)}`;

    container.innerHTML = captures
      .map(
        (c) => `
        <div class="pair-block">
          <h4>${c.direction.toUpperCase()} – ${escapeHtml(c.provider)} ${escapeHtml(c.host)}${escapeHtml(c.path)}
            ${c.status_code ? ` [${c.status_code}]` : ""}
            ${c.latency_ms ? ` ${c.latency_ms.toFixed(0)}ms` : ""}
          </h4>
          <div class="pair-body">${escapeHtml(prettyJson(c.body_text_or_json || ""))}</div>
        </div>
      `
      )
      .join("");

    document.getElementById("capture-detail").classList.remove("hidden");
  } catch (e) {
    console.error("Failed to load capture pair:", e);
  }
}

// ── Sessions View ───────────────────────────────────────
async function loadSessions() {
  const provider = document.getElementById("session-provider-filter").value;

  let url = "/sessions?last=50";
  if (provider) url += `&provider=${encodeURIComponent(provider)}`;

  try {
    const sessions = await api(url);
    renderSessionList(sessions);
    document.getElementById("session-detail").classList.add("hidden");
  } catch (e) {
    console.error("Failed to load sessions:", e);
  }
}

function renderSessionList(sessions) {
  const el = document.getElementById("sessions-list");
  if (!sessions || sessions.length === 0) {
    el.innerHTML = '<div class="empty-state">No sessions yet. Sessions are created when request/response pairs are normalized.</div>';
    return;
  }

  el.innerHTML = sessions
    .map(
      (s) => `
      <div class="session-row" data-session-id="${escapeHtml(s.id)}">
        <span class="session-time">${formatTime(s.started_at)}</span>
        <span class="tag tag-provider">${escapeHtml(s.provider)}</span>
        <span class="session-title">${escapeHtml(s.title_hint || "Untitled session")}</span>
        <span class="session-meta">${s.message_count} msgs</span>
        <span class="session-meta">${escapeHtml(s.tool_family || "")}</span>
      </div>
    `
    )
    .join("");

  el.querySelectorAll(".session-row").forEach((row) => {
    row.addEventListener("click", () => {
      loadSessionMessages(row.dataset.sessionId);
    });
  });
}

async function loadSessionMessages(sessionId) {
  try {
    const messages = await api(`/sessions/${sessionId}/messages`);
    const container = document.getElementById("session-messages");

    document.getElementById("session-title").textContent =
      `Session ${sessionId.slice(0, 8)} – ${messages.length} messages`;

    container.innerHTML = messages
      .map((m) => {
        const roleClass = m.role === "user" ? "user" : m.role === "system" ? "system" : "assistant";
        return `
          <div class="message-bubble ${roleClass}">
            <div class="message-role">${escapeHtml(m.role)}</div>
            <div>${escapeHtml(m.content_text || "")}</div>
            <div class="message-meta">
              ${m.model_name ? `<span>${escapeHtml(m.model_name)}</span>` : ""}
              ${m.token_estimate ? `<span>${m.token_estimate} tokens</span>` : ""}
              <span>${formatShortTime(m.ts)}</span>
            </div>
          </div>
        `;
      })
      .join("");

    document.getElementById("sessions-list").classList.add("hidden");
    document.getElementById("session-detail").classList.remove("hidden");
  } catch (e) {
    console.error("Failed to load session messages:", e);
  }
}

// ── Event Listeners ─────────────────────────────────────
document.getElementById("session-back").addEventListener("click", () => {
  document.getElementById("session-detail").classList.add("hidden");
  document.getElementById("sessions-list").classList.remove("hidden");
});

document.getElementById("capture-back").addEventListener("click", () => {
  document.getElementById("capture-detail").classList.add("hidden");
});

document.getElementById("session-refresh").addEventListener("click", loadSessions);
document.getElementById("capture-refresh").addEventListener("click", loadCaptures);

document.getElementById("session-provider-filter").addEventListener("change", loadSessions);
document.getElementById("capture-provider-filter").addEventListener("change", loadCaptures);
document.getElementById("capture-direction-filter").addEventListener("change", loadCaptures);

// ── Services View ───────────────────────────────────────

const SERVICE_DESCRIPTIONS = {
  core: "FastAPI server providing Ingest & Query APIs and serving this dashboard.",
  proxy: "mitmproxy-based network proxy that captures all AI API traffic.",
  local_hook: "Reverse-proxy for local model servers (Ollama, LM Studio, vLLM, etc.).",
};

async function loadServices() {
  try {
    const data = await api("/services");
    const modeEl = document.getElementById("services-mode");
    const gridEl = document.getElementById("services-grid");

    if (data.mode === "standalone") {
      modeEl.innerHTML = "Running in <strong>standalone</strong> mode. Start with <code>python -m pce_app</code> for full service management.";
    } else {
      modeEl.innerHTML = "Running in <strong>desktop</strong> mode. All services can be controlled below.";
    }

    const services = data.services || {};
    gridEl.innerHTML = Object.entries(services)
      .map(([key, svc]) => {
        const st = svc.status || "stopped";
        const isRunning = st === "running";
        const isCore = key === "core";
        const desc = SERVICE_DESCRIPTIONS[key] || "";

        return `
          <div class="service-card">
            <div class="service-card-header">
              <span class="service-name">${escapeHtml(svc.name)}</span>
              <span class="service-status ${st}">${st}</span>
            </div>
            <div class="service-detail">
              <span>Port: ${svc.port || "-"}</span>
              <span>PID: ${svc.pid || "-"}</span>
              ${svc.error ? `<span style="color:var(--red)">Error: ${escapeHtml(svc.error)}</span>` : ""}
              <span style="margin-top:4px;color:var(--text-muted)">${desc}</span>
            </div>
            <div class="service-actions">
              ${data.mode === "desktop" ? `
                ${!isRunning ? `<button class="btn-service" onclick="serviceAction('${key}','start')">Start</button>` : ""}
                ${isRunning && !isCore ? `<button class="btn-service btn-stop" onclick="serviceAction('${key}','stop')">Stop</button>` : ""}
                ${isCore && isRunning ? `<span style="font-size:11px;color:var(--text-muted)">Core cannot be stopped from dashboard</span>` : ""}
              ` : `<span style="font-size:11px;color:var(--text-muted)">Standalone mode</span>`}
            </div>
          </div>
        `;
      })
      .join("");
  } catch (e) {
    console.error("Failed to load services:", e);
  }
}

async function serviceAction(key, action) {
  try {
    await fetch(`${API}/services/${key}/${action}`, { method: "POST" });
    // Refresh after a brief delay to let the service start/stop
    setTimeout(loadServices, 1000);
  } catch (e) {
    console.error(`Failed to ${action} ${key}:`, e);
  }
}

// Make serviceAction available globally for onclick handlers
window.serviceAction = serviceAction;

// ── Auto-refresh ────────────────────────────────────────
setInterval(() => {
  checkHealth();
  if (currentView === "stats") loadStats();
  if (currentView === "services") loadServices();
}, 15000);

// ── Init ────────────────────────────────────────────────
checkHealth();
loadStats();
