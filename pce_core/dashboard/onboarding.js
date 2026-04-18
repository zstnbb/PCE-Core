// SPDX-License-Identifier: Apache-2.0
/* PCE onboarding wizard controller.
 *
 * Drives the multi-step first-run UI. Talks to the existing
 *   /api/v1/cert, /api/v1/proxy, /api/v1/capture-health, /api/v1/onboarding
 * endpoints — no new concepts are introduced on the back-end.
 */
(function () {
  "use strict";

  // ────────────────────────── Step definitions ──────────────────────────

  const STEPS = [
    {
      id: "welcome",
      label: "Welcome",
      template: "tpl-welcome",
      canSkip: false,
      onEnter: () => {},
    },
    {
      id: "certificate",
      label: "Certificate",
      template: "tpl-certificate",
      canSkip: true,
      onEnter: initCertificateStep,
    },
    {
      id: "extension",
      label: "Extension",
      template: "tpl-extension",
      canSkip: true,
      onEnter: initExtensionStep,
    },
    {
      id: "proxy",
      label: "System Proxy",
      template: "tpl-proxy",
      canSkip: true,
      onEnter: initProxyStep,
    },
    {
      id: "privacy",
      label: "Privacy",
      template: "tpl-privacy",
      canSkip: false,
      onEnter: initPrivacyStep,
    },
    {
      id: "done",
      label: "Done",
      template: "tpl-done",
      canSkip: false,
      onEnter: initDoneStep,
    },
  ];

  const STATE = {
    current: 0,
    backendState: null,
    stepStatus: {}, // {id: "done"|"skipped"|"failed"}
  };

  // ────────────────────────── API helpers ──────────────────────────

  async function api(method, path, body) {
    const opts = { method, headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(path, opts);
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
    if (!resp.ok) {
      const err = new Error(`${resp.status} ${resp.statusText}`);
      err.status = resp.status;
      err.body = data;
      throw err;
    }
    return data;
  }

  const get = (p) => api("GET", p);
  const post = (p, body) => api("POST", p, body);

  // ────────────────────────── Rendering ──────────────────────────

  function renderStepper() {
    const ol = document.getElementById("stepper");
    ol.innerHTML = "";
    STEPS.forEach((step, idx) => {
      const li = document.createElement("li");
      const done = STATE.stepStatus[step.id] === "done";
      const skipped = STATE.stepStatus[step.id] === "skipped";
      const failed = STATE.stepStatus[step.id] === "failed";
      let status = "pending";
      if (idx === STATE.current) status = "current";
      else if (done) status = "done";
      else if (skipped) status = "skipped";
      else if (failed) status = "failed";

      li.dataset.status = status;
      li.dataset.stepId = step.id;
      if (idx < STATE.current) li.dataset.clickable = "true";

      const dot = document.createElement("div");
      dot.className = "wz-step-dot";
      dot.textContent = status === "done" ? "✓" : (idx + 1);
      li.appendChild(dot);

      const label = document.createElement("span");
      label.className = "wz-step-label";
      label.textContent = step.label;
      li.appendChild(label);

      if (idx < STATE.current) {
        li.addEventListener("click", () => goTo(idx));
      }
      ol.appendChild(li);
    });
  }

  function renderFooter() {
    const step = STEPS[STATE.current];
    document.getElementById("btn-back").disabled = STATE.current === 0;
    document.getElementById("btn-step-skip").hidden = !step.canSkip || STATE.current === STEPS.length - 1;
    const next = document.getElementById("btn-next");
    if (STATE.current === STEPS.length - 1) {
      next.textContent = "Finish";
    } else if (STATE.current === STEPS.length - 2) {
      next.textContent = "Review & finish";
    } else {
      next.textContent = "Next →";
    }
  }

  function renderStage() {
    const step = STEPS[STATE.current];
    const stage = document.getElementById("wz-stage");
    const tpl = document.getElementById(step.template);
    stage.innerHTML = "";
    stage.appendChild(tpl.content.cloneNode(true));
    try {
      step.onEnter();
    } catch (err) {
      console.error(`Step ${step.id} onEnter failed:`, err);
      setFooterStatus(`⚠ ${err.message || err}`);
    }
  }

  function render() {
    renderStepper();
    renderFooter();
    renderStage();
    setFooterStatus(`Step ${STATE.current + 1} of ${STEPS.length}`);
  }

  function setFooterStatus(text) {
    document.getElementById("footer-status").textContent = text || "";
  }

  function goTo(idx) {
    if (idx < 0 || idx >= STEPS.length) return;
    STATE.current = idx;
    render();
  }

  async function markStepStatus(stepId, status) {
    STATE.stepStatus[stepId] = status;
    try {
      await post("/api/v1/onboarding/step", { step: stepId, status: status });
    } catch (err) {
      console.warn("onboarding step persist failed:", err);
    }
  }

  // ────────────────────────── Step: Certificate ──────────────────────────

  async function initCertificateStep() {
    document.getElementById("btn-cert-install").addEventListener("click", installCert);
    document.getElementById("btn-cert-refresh").addEventListener("click", refreshCert);
    document.getElementById("btn-cert-export").addEventListener("click", exportCert);
    refreshCert();
  }

  async function refreshCert() {
    const dot = document.getElementById("cert-dot");
    const summary = document.getElementById("cert-summary");
    const kv = document.getElementById("cert-kv");
    dot.dataset.state = "busy";
    summary.textContent = "Checking OS trust store…";
    try {
      const data = await get("/api/v1/cert");
      kv.innerHTML = "";
      addKv(kv, "Platform", data.platform);
      addKv(kv, "Default CA", data.default_ca_path);
      addKv(kv, "Installed", `${data.installed.length} PCE-managed cert(s)`);
      if (data.installed.length > 0) {
        dot.dataset.state = "ok";
        summary.textContent = "PCE CA is installed and trusted.";
        data.installed.slice(0, 3).forEach((c) => {
          addKv(kv, "Thumbprint", c.thumbprint_sha1 || "(unknown)");
        });
      } else {
        dot.dataset.state = "warn";
        summary.textContent = "No PCE CA found in the trust store yet.";
      }
    } catch (err) {
      dot.dataset.state = "error";
      summary.textContent = `Failed to query cert state: ${err.message}`;
    }
  }

  async function installCert() {
    const btn = document.getElementById("btn-cert-install");
    const hint = document.getElementById("cert-hint");
    const cmd = document.getElementById("cert-cmd");
    btn.disabled = true;
    hint.hidden = true; cmd.hidden = true;
    setFooterStatus("Installing certificate…");
    try {
      const res = await post("/api/v1/cert/install", {});
      if (res.ok) {
        await refreshCert();
        await markStepStatus("certificate", "done");
        setFooterStatus("Certificate installed ✓");
      } else if (res.needs_elevation) {
        hint.hidden = false;
        hint.className = "wz-hint wz-hint-warn";
        hint.innerHTML = "<strong>Elevation required.</strong> Run the command below in an admin terminal, then click <em>Refresh status</em>.";
        cmd.hidden = false;
        cmd.textContent = res.command || "(no command string returned)";
        await markStepStatus("certificate", "failed");
      } else {
        hint.hidden = false;
        hint.className = "wz-hint wz-hint-error";
        hint.textContent = res.error || "Install failed — see /api/v1/cert output.";
        await markStepStatus("certificate", "failed");
      }
    } catch (err) {
      hint.hidden = false;
      hint.className = "wz-hint wz-hint-error";
      hint.textContent = `Install request failed: ${err.message}`;
      await markStepStatus("certificate", "failed");
    } finally {
      btn.disabled = false;
    }
  }

  async function exportCert() {
    const dest = prompt(
      "Where should we copy the CA? (full path)",
      getDefaultExportPath(),
    );
    if (!dest) return;
    try {
      const res = await post("/api/v1/cert/export", { dest });
      const hint = document.getElementById("cert-hint");
      hint.hidden = false;
      if (res.ok) {
        hint.className = "wz-hint";
        hint.textContent = `Exported to ${dest}.`;
      } else {
        hint.className = "wz-hint wz-hint-error";
        hint.textContent = res.error || "Export failed.";
      }
    } catch (err) {
      alert(`Export failed: ${err.message}`);
    }
  }

  function getDefaultExportPath() {
    // Best-effort cross-platform guess based on user agent.
    if (navigator.userAgent.includes("Windows")) {
      return "C:/Users/Public/Downloads/pce-ca.pem";
    }
    return "~/Downloads/pce-ca.pem";
  }

  // ────────────────────────── Step: Extension ──────────────────────────

  function initExtensionStep() {
    document.querySelectorAll("[data-open]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const url = btn.dataset.open;
        navigator.clipboard?.writeText(url).catch(() => {});
        btn.textContent = "URL copied ✓";
        setTimeout(() => (btn.textContent = "Copy URL"), 2000);
      });
    });
    document.getElementById("btn-ext-refresh").addEventListener("click", pollExtensionStatus);
    pollExtensionStatus();
  }

  async function pollExtensionStatus() {
    const dot = document.getElementById("ext-dot");
    const summary = document.getElementById("ext-summary");
    dot.dataset.state = "busy";
    summary.textContent = "Checking capture channels…";
    try {
      const health = await get("/api/v1/capture-health");
      const extChan = (health.channels || []).find((c) => c.id === "browser_extension");
      if (!extChan) {
        dot.dataset.state = "warn";
        summary.textContent = "No browser-extension captures ever recorded.";
        return;
      }
      if (extChan.status === "active") {
        dot.dataset.state = "ok";
        summary.textContent = `Extension active — ${extChan.count_5m} captures in the last 5 minutes.`;
        await markStepStatus("extension", "done");
      } else if (extChan.status === "idle" || extChan.status === "stale") {
        dot.dataset.state = "warn";
        summary.textContent = `Extension last seen ${formatAgo(extChan.last_seen_ago_s)} ago. Send a message to re-check.`;
      } else {
        dot.dataset.state = "warn";
        summary.textContent = "Waiting for the extension to send its first capture.";
      }
    } catch (err) {
      dot.dataset.state = "error";
      summary.textContent = `Health probe failed: ${err.message}`;
    }
  }

  function formatAgo(seconds) {
    if (!seconds && seconds !== 0) return "never";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
  }

  // ────────────────────────── Step: Proxy ──────────────────────────

  function initProxyStep() {
    document.getElementById("btn-proxy-enable").addEventListener("click", enableProxy);
    document.getElementById("btn-proxy-disable").addEventListener("click", disableProxy);
    document.getElementById("btn-proxy-refresh").addEventListener("click", refreshProxy);
    refreshProxy();
  }

  async function refreshProxy() {
    const dot = document.getElementById("proxy-dot");
    const summary = document.getElementById("proxy-summary");
    const kv = document.getElementById("proxy-kv");
    dot.dataset.state = "busy";
    summary.textContent = "Reading system proxy state…";
    try {
      const state = await get("/api/v1/proxy");
      kv.innerHTML = "";
      addKv(kv, "Platform", state.platform);
      addKv(kv, "Enabled", String(state.enabled));
      if (state.host) addKv(kv, "Host", `${state.host}:${state.port}`);
      if (state.bypass && state.bypass.length) addKv(kv, "Bypass", state.bypass.join(", "));
      if (state.enabled) {
        dot.dataset.state = "ok";
        summary.textContent = `System proxy pointed at ${state.host}:${state.port}.`;
      } else {
        dot.dataset.state = "warn";
        summary.textContent = "System proxy is not configured.";
      }
    } catch (err) {
      dot.dataset.state = "error";
      summary.textContent = `Failed to query proxy state: ${err.message}`;
    }
  }

  async function enableProxy() {
    const btn = document.getElementById("btn-proxy-enable");
    const hint = document.getElementById("proxy-hint");
    const cmd = document.getElementById("proxy-cmd");
    btn.disabled = true;
    hint.hidden = true; cmd.hidden = true;
    setFooterStatus("Enabling system proxy…");
    try {
      const res = await post("/api/v1/proxy/enable", {});
      if (res.ok) {
        await refreshProxy();
        await markStepStatus("proxy", "done");
        setFooterStatus("System proxy enabled ✓");
      } else if (res.needs_elevation) {
        hint.hidden = false;
        hint.className = "wz-hint wz-hint-warn";
        hint.innerHTML = "<strong>Elevation required.</strong> Run the command below in an admin shell.";
        cmd.hidden = false;
        cmd.textContent = res.command || "(no command returned)";
        await markStepStatus("proxy", "failed");
      } else {
        hint.hidden = false;
        hint.className = "wz-hint wz-hint-error";
        hint.textContent = res.error || "Enable failed.";
        await markStepStatus("proxy", "failed");
      }
    } catch (err) {
      hint.hidden = false;
      hint.className = "wz-hint wz-hint-error";
      hint.textContent = `Enable request failed: ${err.message}`;
      await markStepStatus("proxy", "failed");
    } finally {
      btn.disabled = false;
    }
  }

  async function disableProxy() {
    try {
      await post("/api/v1/proxy/disable", {});
      await refreshProxy();
    } catch (err) {
      alert(`Disable failed: ${err.message}`);
    }
  }

  // ────────────────────────── Step: Privacy ──────────────────────────

  function initPrivacyStep() {
    const prefs = (STATE.backendState && STATE.backendState.preferences) || {};
    document.querySelectorAll("[data-pref]").forEach((el) => {
      const key = el.dataset.pref;
      if (el.type === "checkbox") el.checked = Boolean(prefs[key]);
      else el.value = prefs[key] ?? 0;
      el.addEventListener("change", onPrefChange);
    });
  }

  async function onPrefChange(ev) {
    const el = ev.target;
    const key = el.dataset.pref;
    const value = el.type === "checkbox"
      ? el.checked
      : (el.type === "number" ? Number(el.value) : el.value);
    try {
      const patch = { preferences: { [key]: value } };
      const res = await post("/api/v1/onboarding/state", patch);
      STATE.backendState = res.state;
      setFooterStatus(`Saved “${key}” preference.`);
    } catch (err) {
      setFooterStatus(`Failed to save ${key}: ${err.message}`);
    }
  }

  // ────────────────────────── Step: Done ──────────────────────────

  function initDoneStep() {
    renderDoneSummary();
    document.getElementById("btn-open-dashboard").addEventListener("click", async () => {
      try { await post("/api/v1/onboarding/complete", {}); } catch (_) {}
      window.location.href = "/";
    });
    document.getElementById("btn-download-diag").addEventListener("click", () => {
      window.location.href = "/api/v1/diagnose";
    });
  }

  function renderDoneSummary() {
    const host = document.getElementById("done-summary");
    host.innerHTML = "";
    const labels = {
      certificate: "Certificate",
      extension: "Extension",
      proxy: "System proxy",
      privacy: "Privacy",
    };
    Object.entries(labels).forEach(([id, lbl]) => {
      const item = document.createElement("div");
      item.className = "wz-done-item";
      const title = document.createElement("div");
      title.className = "wz-done-item-title";
      title.textContent = lbl;
      const value = document.createElement("div");
      value.className = "wz-done-item-value";
      value.textContent = renderStatusLabel(STATE.stepStatus[id]);
      item.appendChild(title);
      item.appendChild(value);
      host.appendChild(item);
    });
  }

  function renderStatusLabel(status) {
    switch (status) {
      case "done":    return "Configured ✓";
      case "skipped": return "Skipped";
      case "failed":  return "Needs attention";
      default:        return "Not configured";
    }
  }

  // ────────────────────────── Utilities ──────────────────────────

  function addKv(container, key, value) {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = value ?? "";
    container.appendChild(dt);
    container.appendChild(dd);
  }

  // ────────────────────────── Footer buttons ──────────────────────────

  async function onNext() {
    if (STATE.current === STEPS.length - 1) {
      await post("/api/v1/onboarding/complete", {}).catch(() => {});
      window.location.href = "/";
      return;
    }
    goTo(STATE.current + 1);
  }

  function onBack() {
    goTo(STATE.current - 1);
  }

  async function onStepSkip() {
    const step = STEPS[STATE.current];
    await markStepStatus(step.id, "skipped");
    goTo(STATE.current + 1);
  }

  async function onSkipAll() {
    const ok = confirm(
      "Skip the wizard and jump to the dashboard?\n" +
      "You can revisit the wizard any time via POST /api/v1/onboarding/reset."
    );
    if (!ok) return;
    window.location.href = "/";
  }

  // ────────────────────────── Boot ──────────────────────────

  async function boot() {
    try {
      const payload = await get("/api/v1/onboarding/state");
      STATE.backendState = payload.state || {};
      const saved = STATE.backendState.onboarding_steps || {};
      for (const [id, meta] of Object.entries(saved)) {
        STATE.stepStatus[id] = meta && meta.status ? meta.status : "pending";
      }
    } catch (err) {
      console.warn("onboarding state fetch failed:", err);
    }

    document.getElementById("btn-next").addEventListener("click", onNext);
    document.getElementById("btn-back").addEventListener("click", onBack);
    document.getElementById("btn-step-skip").addEventListener("click", onStepSkip);
    document.getElementById("btn-skip-all").addEventListener("click", onSkipAll);

    render();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
