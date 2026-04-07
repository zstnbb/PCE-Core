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
  if (name === "domains") loadDomains();
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

// ── Lightweight Markdown Renderer ────────────────────────
function renderMarkdown(text) {
  if (!text) return "";

  // Extract and render <thinking> blocks
  let html = text.replace(
    /<thinking>\n?([\s\S]*?)\n?<\/thinking>/g,
    (_, inner) => {
      const rendered = mdToHtml(inner.trim());
      return `<details class="thinking-block"><summary>Thinking</summary><div class="thinking-content">${rendered}</div></details>`;
    }
  );

  // Render the rest as markdown
  html = mdToHtml(html);
  return html;
}

function mdToHtml(src) {
  // Work on a copy
  let s = src;

  // Escape HTML (but preserve our <details> blocks)
  const placeholders = [];
  s = s.replace(/<details[\s\S]*?<\/details>/g, (m) => {
    placeholders.push(m);
    return `%%PH${placeholders.length - 1}%%`;
  });
  s = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // Restore placeholders
  s = s.replace(/%%PH(\d+)%%/g, (_, i) => placeholders[+i]);

  // Fenced code blocks: ```lang\n...\n```
  s = s.replace(/```(\w*)\n([\s\S]*?)\n```/g, (_, lang, code) => {
    return `<pre class="md-codeblock"><code class="lang-${lang || 'text'}">${code}</code></pre>`;
  });

  // Inline code: `code`
  s = s.replace(/`([^`\n]+)`/g, '<code class="md-inline-code">$1</code>');

  // Headings: ### heading
  s = s.replace(/^#### (.+)$/gm, '<h4 class="md-h4">$1</h4>');
  s = s.replace(/^### (.+)$/gm, '<h3 class="md-h3">$1</h3>');
  s = s.replace(/^## (.+)$/gm, '<h2 class="md-h2">$1</h2>');
  s = s.replace(/^# (.+)$/gm, '<h1 class="md-h1">$1</h1>');

  // Bold + italic: ***text*** or ___text___
  s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  // Bold: **text** or __text__
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');
  // Italic: *text* or _text_
  s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
  s = s.replace(/(?<![\w])_(.+?)_(?![\w])/g, '<em>$1</em>');
  // Strikethrough: ~~text~~
  s = s.replace(/~~(.+?)~~/g, '<del>$1</del>');

  // Unordered lists: - item or * item
  s = s.replace(/^([ \t]*)[-*] (.+)$/gm, (_, indent, item) => {
    const depth = Math.floor(indent.length / 2);
    return `<li class="md-li" style="margin-left:${depth * 20}px">${item}</li>`;
  });
  // Wrap consecutive <li> in <ul>
  s = s.replace(/((?:<li[^>]*>.*<\/li>\n?)+)/g, '<ul class="md-ul">$1</ul>');

  // Ordered lists: 1. item
  s = s.replace(/^\d+\. (.+)$/gm, '<li class="md-oli">$1</li>');
  s = s.replace(/((?:<li class="md-oli">.*<\/li>\n?)+)/g, '<ol class="md-ol">$1</ol>');

  // Blockquotes: > text
  s = s.replace(/^&gt; (.+)$/gm, '<blockquote class="md-blockquote">$1</blockquote>');
  // Merge consecutive blockquotes
  s = s.replace(/<\/blockquote>\n<blockquote class="md-blockquote">/g, '<br>');

  // Horizontal rule: --- or ***
  s = s.replace(/^(---|\*\*\*|___)$/gm, '<hr class="md-hr">');

  // Links: [text](url)
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="md-link">$1</a>');

  // Line breaks: double newline → paragraph break
  s = s.replace(/\n\n/g, '</p><p class="md-p">');
  // Single newline → <br> (within text, not inside pre/code)
  s = s.replace(/(?<!<\/(pre|ul|ol|li|blockquote|h[1-4]|hr|details|div|p|summary)>)\n(?!<)/g, '<br>');

  // Wrap in paragraph
  s = '<p class="md-p">' + s + '</p>';
  // Clean up empty paragraphs
  s = s.replace(/<p class="md-p">\s*<\/p>/g, '');

  return s;
}

// ── Animated counter ────────────────────────────────────
function animateValue(elementId, target) {
  const el = document.getElementById(elementId);
  const current = parseInt(el.textContent) || 0;
  if (current === target) return;
  const diff = target - current;
  const steps = Math.min(Math.abs(diff), 20);
  const duration = 400;
  const stepTime = duration / steps;
  let step = 0;
  const timer = setInterval(() => {
    step++;
    const progress = step / steps;
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(current + diff * eased).toLocaleString();
    if (step >= steps) {
      el.textContent = target.toLocaleString();
      clearInterval(timer);
    }
  }, stepTime);
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

    animateValue("stat-total", stats.total_captures);
    animateValue("stat-sessions", sessions.length);
    animateValue("stat-providers", Object.keys(stats.by_provider || {}).length);
    animateValue("stat-sources", Object.keys(stats.by_source || {}).length);

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
  const max = sorted[0][1] || 1;
  el.innerHTML = sorted
    .map(
      ([key, val]) => {
        const pct = Math.max((val / max) * 100, 2);
        return `<div class="breakdown-item">
          <div class="breakdown-item-header">
            <span class="key">${escapeHtml(key)}</span>
            <span class="val">${val.toLocaleString()}</span>
          </div>
          <div class="breakdown-bar"><div class="breakdown-bar-fill" style="width:${pct}%"></div></div>
        </div>`;
      }
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
        <span class="capture-host">${escapeHtml(c.host || "")}</span>
        <span class="capture-path">${escapeHtml(c.path || "")}</span>
        ${c.model_name ? `<span class="capture-model">${escapeHtml(c.model_name)}</span>` : ""}
        ${c.status_code ? `<span class="tag tag-response">${c.status_code}</span>` : ""}
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
        const contentHtml = roleClass === "user"
          ? escapeHtml(m.content_text || "")
          : renderMarkdown(m.content_text || "");
        return `
          <div class="message-bubble ${roleClass}">
            <div class="message-role">${escapeHtml(m.role)}</div>
            <div class="message-content">${contentHtml}</div>
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

// ── Domains View ───────────────────────────────────────

async function loadDomains() {
  try {
    const data = await api("/domains");

    const modeEl = document.getElementById("domain-mode");
    modeEl.innerHTML = `Capture mode: <strong>${escapeHtml(data.capture_mode)}</strong> | Static: ${data.static_domains.length} | Custom: ${data.custom_domains.length}`;

    renderCustomDomains(data.custom_domains);
    renderStaticDomains(data.static_domains);
  } catch (e) {
    console.error("Failed to load domains:", e);
  }
}

function renderCustomDomains(domains) {
  const el = document.getElementById("custom-domains-list");
  if (!domains || domains.length === 0) {
    el.innerHTML = '<div class="empty-state">No custom domains. Add one above or let SMART mode discover them automatically.</div>';
    return;
  }
  el.innerHTML = domains
    .map(
      (d) => `
      <div class="domain-row">
        <span class="domain-name">${escapeHtml(d.domain)}</span>
        <span class="tag tag-provider">${escapeHtml(d.source)}</span>
        ${d.confidence ? `<span class="tag">${escapeHtml(d.confidence)}</span>` : ""}
        <span class="domain-meta">${formatTime(d.added_at)}</span>
        ${d.reason ? `<span class="domain-reason">${escapeHtml(d.reason)}</span>` : ""}
        <button class="btn-sm btn-remove" onclick="removeDomain('${escapeHtml(d.domain)}')">Remove</button>
      </div>
    `
    )
    .join("");
}

function renderStaticDomains(domains) {
  const el = document.getElementById("static-domains-list");
  if (!domains || domains.length === 0) {
    el.innerHTML = '<div class="empty-state">No static domains.</div>';
    return;
  }
  el.innerHTML = domains
    .map(
      (d) => `
      <div class="domain-row static">
        <span class="domain-name">${escapeHtml(d)}</span>
        <span class="tag">built-in</span>
      </div>
    `
    )
    .join("");
}

async function addDomain() {
  const input = document.getElementById("domain-add-input");
  const domain = input.value.trim().toLowerCase();
  if (!domain) return;
  try {
    await fetch(`${API}/domains`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, source: "user" }),
    });
    input.value = "";
    loadDomains();
  } catch (e) {
    console.error("Failed to add domain:", e);
  }
}

async function removeDomain(domain) {
  try {
    await fetch(`${API}/domains/${encodeURIComponent(domain)}`, { method: "DELETE" });
    loadDomains();
  } catch (e) {
    console.error("Failed to remove domain:", e);
  }
}

window.removeDomain = removeDomain;

document.getElementById("domain-add-btn").addEventListener("click", addDomain);
document.getElementById("domain-add-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") addDomain();
});
document.getElementById("domain-refresh-btn").addEventListener("click", loadDomains);

// ── Services / Capabilities View ────────────────────────

const CAP_ICONS = {
  server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
  cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>',
  clipboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
};

const SERVICE_DESCRIPTIONS = {
  core: "FastAPI server providing Ingest & Query APIs and serving this dashboard.",
  proxy: "mitmproxy-based network proxy that captures all AI API traffic.",
  local_hook: "Reverse-proxy for a single local model server (Ollama default).",
  multi_hook: "Auto-discovers and proxies all running local model servers (multi-port).",
  clipboard: "Monitors clipboard for AI conversation text (experimental).",
};

function formatRelativeTime(ts) {
  if (!ts) return "Never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function renderCapabilityCard(cap) {
  const st = cap.status; // connected | disconnected | info
  const icon = CAP_ICONS[cap.icon] || CAP_ICONS.server;
  const badgeLabel = st === "connected" ? "Connected" : st === "info" ? "Available" : "Disconnected";

  let statsHtml = "";
  if (cap.capture_count !== null) {
    statsHtml = `
      <div class="cap-card-stats">
        <div class="cap-stat">
          <span class="cap-stat-value">${(cap.capture_count || 0).toLocaleString()}</span>
          <span class="cap-stat-label">Captures</span>
        </div>
        <div class="cap-stat">
          <span class="cap-stat-value">${formatRelativeTime(cap.last_seen)}</span>
          <span class="cap-stat-label">Last Seen</span>
        </div>
        ${cap.port ? `<div class="cap-stat"><span class="cap-stat-value">${cap.port}</span><span class="cap-stat-label">Port</span></div>` : ""}
      </div>`;
  }

  let setupDetailHtml = "";
  if (cap.setup_detail) {
    const steps = Object.entries(cap.setup_detail)
      .map(([k, v]) => `<div class="setup-step"><span class="setup-key">${escapeHtml(k)}:</span><span>${escapeHtml(v)}</span></div>`)
      .join("");
    setupDetailHtml = `<div class="cap-setup-detail">${steps}</div>`;
  }

  return `
    <div class="cap-card status-${st}">
      <div class="cap-card-header">
        <div class="cap-card-icon">${icon}</div>
        <div class="cap-card-title">
          <div class="cap-card-name">${escapeHtml(cap.name)}</div>
          <div class="cap-card-desc">${escapeHtml(cap.description)}</div>
        </div>
        <span class="cap-card-badge ${st}">${badgeLabel}</span>
      </div>
      ${statsHtml}
      <div class="cap-card-setup">${escapeHtml(cap.setup)}${setupDetailHtml}</div>
    </div>`;
}

async function loadServices() {
  try {
    const [capData, svcData] = await Promise.all([
      api("/capabilities"),
      api("/services"),
    ]);

    // Mode banner
    const modeEl = document.getElementById("services-mode");
    if (capData.mode === "standalone") {
      modeEl.innerHTML = `Running in <strong>standalone</strong> mode. Capture mode: <strong>${escapeHtml(capData.capture_mode)}</strong>. Start with <code>python -m pce_app</code> for full service management.`;
    } else {
      modeEl.innerHTML = `Running in <strong>desktop</strong> mode. Capture mode: <strong>${escapeHtml(capData.capture_mode)}</strong>. All services can be controlled below.`;
    }

    // Summary chips
    const caps = capData.capabilities || [];
    const connected = caps.filter((c) => c.status === "connected").length;
    const total = caps.filter((c) => c.status !== "info").length;
    const summaryEl = document.getElementById("cap-summary");
    summaryEl.innerHTML = caps
      .filter((c) => c.status !== "info")
      .map((c) => `
        <div class="cap-summary-chip ${c.status}">
          <span class="cap-summary-dot ${c.status}"></span>
          ${escapeHtml(c.name)}
        </div>`)
      .join("") +
      `<div class="cap-summary-chip" style="margin-left:auto;color:var(--text-secondary)">
        <strong>${connected}</strong>/${total} channels active
      </div>`;

    // Capability cards
    const capGridEl = document.getElementById("capabilities-grid");
    capGridEl.innerHTML = caps.map(renderCapabilityCard).join("");

    // Service control grid (only in desktop mode with actual services)
    const services = svcData.services || {};
    const gridEl = document.getElementById("services-grid");
    const svcEntries = Object.entries(services);
    if (svcEntries.length === 0 || (svcEntries.length === 1 && svcEntries[0][0] === "core")) {
      gridEl.innerHTML = '<div class="empty-state">Service control is available in desktop mode (<code>python -m pce_app</code>).</div>';
    } else {
      gridEl.innerHTML = svcEntries
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
                ${svcData.mode === "desktop" ? `
                  ${!isRunning ? `<button class="btn-service" onclick="serviceAction('${key}','start')">Start</button>` : ""}
                  ${isRunning && !isCore ? `<button class="btn-service btn-stop" onclick="serviceAction('${key}','stop')">Stop</button>` : ""}
                  ${isCore && isRunning ? `<span style="font-size:11px;color:var(--text-muted)">Core cannot be stopped from dashboard</span>` : ""}
                ` : `<span style="font-size:11px;color:var(--text-muted)">Standalone mode</span>`}
              </div>
            </div>`;
        })
        .join("");
    }
  } catch (e) {
    console.error("Failed to load services:", e);
  }
}

async function serviceAction(key, action) {
  try {
    await fetch(`${API}/services/${key}/${action}`, { method: "POST" });
    setTimeout(loadServices, 1000);
  } catch (e) {
    console.error(`Failed to ${action} ${key}:`, e);
  }
}

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
