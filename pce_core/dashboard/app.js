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

  // Load capture health panel (independent, non-blocking)
  loadCaptureHealth();
}

// ── Capture Health Panel ─────────────────────────────────
async function loadCaptureHealth() {
  try {
    const data = await api("/capture-health");
    renderCaptureHealth(data);
  } catch (e) {
    // Health endpoint may not exist on older server versions
    document.getElementById("health-channels").innerHTML =
      '<div class="empty-state">Health data unavailable</div>';
  }
}

function formatAgo(seconds) {
  if (!seconds && seconds !== 0) return "never";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function renderCaptureHealth(data) {
  const { channels, recent_providers, normalization, summary } = data;

  // Summary
  const summaryEl = document.getElementById("health-summary");
  summaryEl.textContent = `${summary.active_channels}/${summary.total_channels} active`;

  // Channels
  const channelsEl = document.getElementById("health-channels");
  channelsEl.innerHTML = channels
    .map((ch) => {
      // Status label: show count with time window, or a descriptive status
      let countStr;
      if (ch.count_5m > 0) {
        countStr = `<span class="count-active">${ch.count_5m}</span> in 5m`;
      } else if (ch.count_1h > 0) {
        countStr = `${ch.count_1h} in 1h`;
      } else if (ch.count_24h > 0) {
        countStr = `${ch.count_24h} in 24h`;
      } else if (ch.total > 0) {
        countStr = `<span class="count-dim">${ch.total} total</span>`;
      } else {
        countStr = `<span class="count-dim">No data</span>`;
      }

      // Time ago — show readable text, replace raw "never"
      const agoStr = ch.last_seen_ago_s != null
        ? formatAgo(ch.last_seen_ago_s)
        : `<span class="text-muted">Not yet active</span>`;

      let subHtml = "";
      if (ch.sub_channels) {
        const subs = Object.values(ch.sub_channels);
        subHtml = `<div class="health-sub-channels">${subs
          .map((s) => {
            const sCount = s.count_5m || s.count_1h || s.count_24h || 0;
            return `<span class="health-sub ${s.status}">${escapeHtml(s.label)}: ${sCount}</span>`;
          })
          .join("")}</div>`;
      }

      return `
        <div class="health-channel ${ch.status}">
          <div class="health-dot ${ch.status}"></div>
          <div class="health-channel-info">
            <div class="health-channel-name">${escapeHtml(ch.label)}</div>
            <div class="health-channel-detail">${agoStr}${subHtml}</div>
          </div>
          <div class="health-channel-counts">${countStr}</div>
        </div>
      `;
    })
    .join("");

  // Normalization
  const normEl = document.getElementById("health-normalization");
  if (normalization && normalization.pairs_24h > 0) {
    const rate = normalization.success_rate != null
      ? `<span class="norm-rate">${(normalization.success_rate * 100).toFixed(0)}%</span>`
      : "—";
    normEl.innerHTML = `Normalization: ${normalization.sessions_24h} sessions / ${normalization.pairs_24h} pairs (${rate})`;
  } else {
    normEl.innerHTML = "Normalization: no activity in 24h";
  }

  // Active providers
  const provEl = document.getElementById("health-providers");
  if (recent_providers && recent_providers.length > 0) {
    provEl.innerHTML = recent_providers
      .slice(0, 6)
      .map(
        (p) =>
          `<span class="health-provider-tag">${escapeHtml(p.provider)} (${p.count_1h})</span>`
      )
      .join("");
  } else {
    provEl.innerHTML = '<span style="font-size:11px;color:var(--text-muted)">No providers active in 1h</span>';
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
    document.getElementById("sessions-list").classList.remove("hidden");
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

    // Count attachments for header
    let attCount = 0;
    messages.forEach((m) => {
      if (m.content_json) {
        try { attCount += getRenderableAttachments(JSON.parse(m.content_json)).length; } catch {}
      }
    });
    const attLabel = attCount > 0 ? ` · ${attCount} attachments` : "";
    document.getElementById("session-title").textContent =
      `Session ${sessionId.slice(0, 8)} – ${messages.length} messages${attLabel}`;

    container.innerHTML = messages
      .map((m) => {
        const roleClass = m.role === "user" ? "user" : m.role === "system" ? "system" : "assistant";
        const contentHtml = roleClass === "user"
          ? escapeHtml(m.content_text || "")
          : renderMarkdown(m.content_text || "");
        const attachmentsHtml = m.content_json ? renderAttachments(m.content_json) : "";
        return `
          <div class="message-bubble ${roleClass}">
            <div class="message-role">${escapeHtml(m.role)}</div>
            <div class="message-content">${contentHtml}</div>
            ${attachmentsHtml}
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

// ── Attachment Rendering ─────────────────────────────────
const ATT_ICONS = {
  image_url:         '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  image_generation:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  code_block:        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
  code_output:       '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
  tool_call:         '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
  tool_result:       '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
  citation:          '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  file:              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
  document:          '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
  audio:             '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>',
  canvas:            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>',
};

function getRenderableAttachments(data) {
  if (Array.isArray(data?.attachments)) return data.attachments;

  const blocks = data?.rich_content?.blocks;
  if (!Array.isArray(blocks)) return [];
  return blocks
    .map((block) => {
      if (!block || typeof block !== "object") return null;
      if (block.data && typeof block.data === "object") return block.data;
      return { ...block, type: block.attachment_type || block.type || "unknown" };
    })
    .filter(Boolean);
}

function renderAttachments(contentJsonStr) {
  let data;
  try {
    data = JSON.parse(contentJsonStr);
  } catch {
    return "";
  }
  const attachments = getRenderableAttachments(data);
  if (!Array.isArray(attachments) || attachments.length === 0) return "";

  const cards = attachments.map((att) => renderOneAttachment(att)).filter(Boolean);
  if (cards.length === 0) return "";
  return `<div class="att-container">${cards.join("")}</div>`;
}

function renderOneAttachment(att) {
  const type = att.type || "unknown";
  const icon = ATT_ICONS[type] || ATT_ICONS.file;
  const typeLabel = type.replace(/_/g, " ");

  switch (type) {
    case "citation":
      return renderCitation(att, icon);
    case "code_block":
      return renderCodeBlock(att, icon);
    case "code_output":
      return renderCodeOutput(att, icon);
    case "image_url":
    case "image_generation":
      return renderImage(att, icon, typeLabel);
    case "tool_call":
      return renderToolCall(att, icon);
    case "tool_result":
      return renderToolResult(att, icon);
    case "file":
    case "document":
      return renderFile(att, icon, typeLabel);
    case "audio":
      return renderAudio(att, icon);
    case "canvas":
      return renderCanvas(att, icon);
    default:
      return renderGenericAttachment(att, icon, typeLabel);
  }
}

function renderCitation(att, icon) {
  const url = att.url || "";
  const title = att.title || url || "Citation";
  const text = att.text ? escapeHtml(att.text).slice(0, 120) : "";
  const linkHtml = url
    ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener" class="att-link">${escapeHtml(title)}</a>`
    : `<span class="att-title">${escapeHtml(title)}</span>`;
  return `
    <div class="att-card att-citation">
      <div class="att-header">${icon}<span class="att-type">Citation</span></div>
      <div class="att-body">${linkHtml}${text ? `<div class="att-text">${text}</div>` : ""}</div>
    </div>`;
}

function renderCodeBlock(att, icon) {
  const lang = att.language || "text";
  const code = att.code || "";
  return `
    <div class="att-card att-code">
      <div class="att-header">${icon}<span class="att-type">Code</span><span class="att-lang">${escapeHtml(lang)}</span></div>
      <pre class="att-codeblock"><code>${escapeHtml(code)}</code></pre>
    </div>`;
}

function renderCodeOutput(att, icon) {
  const output = att.output || "";
  return `
    <div class="att-card att-code-output">
      <div class="att-header">${icon}<span class="att-type">Output</span></div>
      <pre class="att-codeblock att-output-pre">${escapeHtml(output)}</pre>
    </div>`;
}

function isOpenableAttachmentUrl(url, type = "file") {
  if (!url || typeof url !== "string") return false;
  if (/^https?:\/\//i.test(url)) return true;
  if (type === "image" && /^data:image\//i.test(url)) return true;
  return false;
}

function renderImage(att, icon, typeLabel) {
  const url = att.url || "";
  const alt = att.alt || att.detail || "";
  const ref = att.file_id || url;
  const truncatedUrl = ref.length > 80 ? ref.slice(0, 80) + "..." : ref;
  const isViewable = isOpenableAttachmentUrl(url, "image");
  const openAction = isViewable
    ? `<div class="att-actions"><a class="att-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">Open image</a></div>`
    : (truncatedUrl ? `<div class="att-actions"><span class="att-ref">${escapeHtml(truncatedUrl)}</span></div>` : "");
  const imgHtml = isViewable
    ? `<a class="att-image-link" href="${escapeHtml(url)}" target="_blank" rel="noopener"><img src="${escapeHtml(url)}" alt="${escapeHtml(alt)}" class="att-image-preview" loading="lazy" /></a>`
    : `<div class="att-image-placeholder">${icon}<span>${escapeHtml(truncatedUrl || "Image reference")}</span></div>`;
  return `
    <div class="att-card att-image">
      <div class="att-header">${icon}<span class="att-type">${escapeHtml(typeLabel)}</span>${alt ? `<span class="att-detail">${escapeHtml(alt)}</span>` : ""}</div>
      <div class="att-body">${imgHtml}${openAction}</div>
    </div>`;
}

function renderToolCall(att, icon) {
  const name = att.name || "unknown";
  const args = att.arguments || "";
  let argsHtml = "";
  if (args) {
    try {
      argsHtml = escapeHtml(JSON.stringify(JSON.parse(args), null, 2));
    } catch {
      argsHtml = escapeHtml(args);
    }
  }
  return `
    <div class="att-card att-tool-call">
      <div class="att-header">${icon}<span class="att-type">Tool Call</span><span class="att-fn-name">${escapeHtml(name)}</span></div>
      ${argsHtml ? `<pre class="att-codeblock">${argsHtml}</pre>` : ""}
    </div>`;
}

function renderToolResult(att, icon) {
  const content = att.content || "";
  const isError = att.is_error;
  return `
    <div class="att-card att-tool-result ${isError ? "att-error" : ""}">
      <div class="att-header">${icon}<span class="att-type">Tool Result</span>${isError ? '<span class="att-error-badge">Error</span>' : ""}</div>
      ${content ? `<pre class="att-codeblock">${escapeHtml(content.slice(0, 2000))}</pre>` : ""}
    </div>`;
}

function renderFile(att, icon, typeLabel) {
  const name = att.name || att.title || "File";
  const mediaType = att.media_type || att.source_type || "";
  const url = att.url || "";
  const ref = att.file_id || url || "";
  const isOpenable = isOpenableAttachmentUrl(url);
  const titleHtml = isOpenable
    ? `<a class="att-link att-file-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(name)}</a>`
    : `<span class="att-filename">${escapeHtml(name)}</span>`;
  const metaParts = [mediaType, att.file_id || ""].filter(Boolean);
  const actionHtml = isOpenable
    ? `<div class="att-actions"><a class="att-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">Open file</a></div>`
    : (ref ? `<div class="att-actions"><span class="att-ref">${escapeHtml(ref.length > 96 ? ref.slice(0, 96) + "..." : ref)}</span></div>` : "");
  return `
    <div class="att-card att-file">
      <div class="att-header">${icon}<span class="att-type">${escapeHtml(typeLabel)}</span></div>
      <div class="att-body">${titleHtml}${metaParts.length ? `<span class="att-detail">${escapeHtml(metaParts.join(" · "))}</span>` : ""}${actionHtml}</div>
    </div>`;
}

function renderAudio(att, icon) {
  const format = att.format || "";
  const transcript = att.transcript || "";
  return `
    <div class="att-card att-audio">
      <div class="att-header">${icon}<span class="att-type">Audio</span>${format ? `<span class="att-detail">${escapeHtml(format)}</span>` : ""}</div>
      ${transcript ? `<div class="att-body att-transcript">${escapeHtml(transcript.slice(0, 500))}</div>` : ""}
    </div>`;
}

function renderCanvas(att, icon) {
  const content = att.content || "";
  return `
    <div class="att-card att-canvas">
      <div class="att-header">${icon}<span class="att-type">Canvas / Artifact</span></div>
      ${content ? `<pre class="att-codeblock">${escapeHtml(content.slice(0, 3000))}</pre>` : ""}
    </div>`;
}

function renderGenericAttachment(att, icon, typeLabel) {
  const raw = att.raw || "";
  return `
    <div class="att-card">
      <div class="att-header">${icon}<span class="att-type">${escapeHtml(typeLabel)}</span></div>
      ${raw ? `<pre class="att-codeblock">${escapeHtml(raw.slice(0, 1000))}</pre>` : ""}
    </div>`;
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

// ── Dev: Reset Baseline ─────────────────────────────────
document.getElementById("btn-reset-baseline")?.addEventListener("click", async () => {
  const btn = document.getElementById("btn-reset-baseline");
  const status = document.getElementById("reset-status");

  if (!confirm("This will DELETE all captures, sessions, and messages.\n\nAre you sure?")) {
    return;
  }

  btn.disabled = true;
  btn.textContent = "Resetting...";
  status.textContent = "";

  try {
    const resp = await fetch(`${API}/dev/reset`, { method: "POST" });
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    const data = await resp.json();
    status.textContent = `Cleared ${data.captures_deleted} captures, ${data.sessions_deleted} sessions, ${data.messages_deleted} messages`;
    status.style.color = "var(--green)";
    loadStats();
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
    status.style.color = "var(--red)";
  } finally {
    btn.disabled = false;
    btn.textContent = "Reset to Baseline";
  }
});

// ── Auto-refresh ────────────────────────────────────────
setInterval(() => {
  checkHealth();
  if (currentView === "stats") loadStats();
  if (currentView === "services") loadServices();
}, 15000);

// ── Init ────────────────────────────────────────────────
checkHealth();
loadStats();
