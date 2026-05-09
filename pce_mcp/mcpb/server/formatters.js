// SPDX-License-Identifier: Apache-2.0
/**
 * Pure formatting helpers — no I/O, no side-effects.
 *
 * Each helper mirrors the Python equivalent in pce_mcp/server.py so the
 * UX is consistent whether the user reaches PCE via the Python direct
 * path or via the Node .mcpb proxy.
 */

function _fmtEpoch(epochSec, withDate = true) {
  if (typeof epochSec !== "number" || !Number.isFinite(epochSec)) return "?";
  const d = new Date(epochSec * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  const hms = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  if (!withDate) return hms;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hms}`;
}

export function formatCapturesList(rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return "No captures found matching the criteria.";
  }
  const lines = [`Found ${rows.length} capture(s):`, ""];
  for (const r of rows) {
    const ts = _fmtEpoch(r.created_at);
    let line =
      `- [${ts}] ${r.direction} ${r.provider || "?"} ` +
      `${r.host || ""}${r.path || ""} model=${r.model_name || "?"} ` +
      `pair=${(r.pair_id || "").slice(0, 8)}`;
    if (r.status_code != null && r.status_code !== "") {
      line += ` status=${r.status_code}`;
    }
    lines.push(line);
  }
  return lines.join("\n");
}

export function formatStats(stats) {
  if (!stats || typeof stats !== "object") {
    return "No stats available.";
  }
  const lines = [`Total captures: ${stats.total_captures ?? 0}`, "", "By provider:"];
  const entries = (obj) =>
    Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  for (const [p, c] of entries(stats.by_provider)) lines.push(`  ${p}: ${c}`);
  lines.push("", "By direction:");
  for (const [d, c] of entries(stats.by_direction)) lines.push(`  ${d}: ${c}`);
  lines.push("", "By source:");
  for (const [s, c] of entries(stats.by_source)) lines.push(`  ${s}: ${c}`);
  return lines.join("\n");
}

export function formatSessions(rows) {
  if (!Array.isArray(rows) || rows.length === 0) return "No sessions found.";
  const lines = [`Found ${rows.length} session(s):`, ""];
  for (const s of rows) {
    const ts = _fmtEpoch(s.started_at).slice(0, 16); // date + HH:MM
    const title = (s.title_hint || "untitled").slice(0, 60);
    lines.push(
      `- [${ts}] ${s.provider || "?"} | ${title} | ` +
        `${s.message_count ?? 0} msgs | id=${(s.id || "").slice(0, 8)}`,
    );
  }
  return lines.join("\n");
}

export function formatMessages(sessionId, msgs) {
  if (!Array.isArray(msgs) || msgs.length === 0) {
    return `No messages found for session ${sessionId}.`;
  }
  const lines = [
    `Session ${sessionId.slice(0, 8)} — ${msgs.length} message(s):`,
    "",
  ];
  for (const m of msgs) {
    const ts = _fmtEpoch(m.ts, false);
    const content = (m.content_text || "").slice(0, 200);
    const modelTag = m.model_name ? ` [${m.model_name}]` : "";
    const tokenTag = m.token_estimate ? ` (${m.token_estimate}tok)` : "";
    lines.push(`[${ts}] ${m.role || "?"}${modelTag}${tokenTag}: ${content}`);
  }
  return lines.join("\n");
}

export function formatPair(pairId, rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return `No captures found for pair_id=${pairId}.`;
  }
  const lines = [`Pair ${pairId.slice(0, 8)} — ${rows.length} capture(s):`, ""];
  for (const r of rows) {
    const ts = _fmtEpoch(r.created_at);
    const bodyPreview = (r.body_text_or_json || "").slice(0, 300);
    lines.push(
      `--- ${(r.direction || "?").toUpperCase()} [${ts}] ---`,
      `Provider: ${r.provider || "?"} | Host: ${r.host || ""}${r.path || ""}`,
      `Model: ${r.model_name || "?"} | Status: ${r.status_code ?? "-"}`,
      `Body preview: ${bodyPreview}`,
      "",
    );
  }
  return lines.join("\n");
}
