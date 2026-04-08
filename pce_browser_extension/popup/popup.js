/**
 * PCE Browser Extension – Popup script
 *
 * Handles:
 *   - Server status display
 *   - Capture enable/disable toggle
 *   - Current page AI detection status
 *   - "Capture This Page" manual trigger
 *   - Site blacklist management
 */

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const serverStatus = document.getElementById("server-status");
const captureCount = document.getElementById("capture-count");
const enabledToggle = document.getElementById("enabled-toggle");
const lastError = document.getElementById("last-error");
const dashboardLink = document.getElementById("dashboard-link");
const pageDetectionIcon = document.getElementById("page-detection-icon");
const pageDomain = document.getElementById("page-domain");
const pageDetectionText = document.getElementById("page-detection-text");
const capturePageBtn = document.getElementById("capture-page-btn");
const blacklistBtn = document.getElementById("blacklist-btn");
const unblacklistBtn = document.getElementById("unblacklist-btn");

const DASHBOARD_URL = "http://127.0.0.1:9800";
dashboardLink.href = DASHBOARD_URL;
dashboardLink.addEventListener("click", (e) => {
  e.preventDefault();
  chrome.tabs.create({ url: DASHBOARD_URL });
});

let currentTabId = null;
let currentDomain = null;
let currentBlacklist = [];

// ---------------------------------------------------------------------------
// 1. Load status from service worker
// ---------------------------------------------------------------------------
chrome.runtime.sendMessage({ type: "PCE_GET_STATUS" }, (response) => {
  if (chrome.runtime.lastError || !response) return;

  enabledToggle.checked = response.enabled;
  captureCount.textContent = response.captureCount || 0;

  if (response.serverOnline) {
    serverStatus.textContent = "Online";
    serverStatus.className = "status-badge online";
  } else {
    serverStatus.textContent = "Offline";
    serverStatus.className = "status-badge offline";
  }

  if (response.lastError) {
    lastError.textContent = response.lastError;
    lastError.title = response.lastError;
  }

  // Show self-check warnings for silent domains
  const silentWarnings = document.getElementById("silent-warnings");
  if (response.silentDomains && response.silentDomains.length > 0) {
    silentWarnings.style.display = "";
    silentWarnings.innerHTML = response.silentDomains
      .map(
        (s) =>
          `<div class="warning-item">⚠ ${s.domain}: detected ${s.age_s}s ago, 0 captures</div>`
      )
      .join("");
  }
});

// ---------------------------------------------------------------------------
// 2. Toggle handler
// ---------------------------------------------------------------------------
enabledToggle.addEventListener("change", () => {
  chrome.runtime.sendMessage(
    { type: "PCE_SET_ENABLED", enabled: enabledToggle.checked },
    () => {
      if (chrome.runtime.lastError) return;
    }
  );
});

// ---------------------------------------------------------------------------
// 3. Current page detection status
// ---------------------------------------------------------------------------
async function checkCurrentPage() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url) {
      setPageStatus("unknown", "-", "Cannot access this page");
      return;
    }

    currentTabId = tab.id;

    // Extract domain
    try {
      const url = new URL(tab.url);
      currentDomain = url.hostname;
      pageDomain.textContent = currentDomain;
    } catch {
      currentDomain = null;
      setPageStatus("unknown", tab.url.slice(0, 30), "Invalid URL");
      return;
    }

    // Chrome internal pages
    if (tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://") || tab.url.startsWith("about:")) {
      setPageStatus("not-detected", currentDomain, "System page - cannot capture");
      return;
    }

    // Load blacklist
    await loadBlacklist();

    if (currentBlacklist.includes(currentDomain)) {
      setPageStatus("blocked", currentDomain, "Blocked by you");
      blacklistBtn.style.display = "none";
      unblacklistBtn.style.display = "";
      capturePageBtn.disabled = true;
      return;
    }

    // Query the tab's content script for detection results
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: currentTabId },
        func: () => {
          if (window.__PCE_DETECTOR) {
            return window.__PCE_DETECTOR.detectAIPage();
          }
          return null;
        },
      });

      const detection = results?.[0]?.result;

      if (detection && detection.detected) {
        setPageStatus("detected", currentDomain,
          `AI page (${detection.confidence}, score=${detection.score})`);
        capturePageBtn.disabled = false;
        blacklistBtn.style.display = "";
      } else if (detection) {
        setPageStatus("not-detected", currentDomain,
          `Not detected (score=${detection.score})`);
        capturePageBtn.disabled = false; // still allow manual capture
        blacklistBtn.style.display = "";
      } else {
        // Detector not loaded — page may not have been scanned yet
        setPageStatus("unknown", currentDomain, "Detector not loaded on this page");
        capturePageBtn.disabled = false;
        blacklistBtn.style.display = "";
      }
    } catch {
      // Cannot inject into this page (e.g., chrome web store)
      setPageStatus("not-detected", currentDomain, "Cannot access page content");
      capturePageBtn.disabled = true;
    }
  } catch (err) {
    setPageStatus("unknown", "-", err.message);
  }
}

function setPageStatus(state, domain, text) {
  pageDomain.textContent = domain || "-";
  pageDetectionText.textContent = text || "";

  pageDetectionIcon.className = "detection-icon";
  if (state === "detected") {
    pageDetectionIcon.textContent = "\u2713";
    pageDetectionIcon.classList.add("detected");
  } else if (state === "blocked") {
    pageDetectionIcon.textContent = "\u2717";
    pageDetectionIcon.classList.add("blocked");
  } else if (state === "not-detected") {
    pageDetectionIcon.textContent = "-";
    pageDetectionIcon.classList.add("not-detected");
  } else {
    pageDetectionIcon.textContent = "?";
  }
}

// Run detection check
checkCurrentPage();

// ---------------------------------------------------------------------------
// 4. "Capture This Page" manual trigger
// ---------------------------------------------------------------------------
capturePageBtn.addEventListener("click", async () => {
  if (!currentTabId) return;

  capturePageBtn.disabled = true;
  capturePageBtn.textContent = "Capturing...";

  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: currentTabId },
      func: () => {
        // Try to trigger universal extractor or existing extractor
        if (window.__PCE_UNIVERSAL_EXTRACTOR_LOADED) {
          // Already loaded, trigger manual capture via a DOM mutation hint
          document.dispatchEvent(new CustomEvent("pce-manual-capture"));
          return { method: "universal_extractor" };
        }
        return { method: "none", needsInjection: true };
      },
    });

    const result = results?.[0]?.result;

    if (result?.needsInjection) {
      // Inject capture scripts and then trigger
      chrome.runtime.sendMessage(
        {
          type: "PCE_AI_PAGE_DETECTED",
          payload: {
            url: "",
            domain: currentDomain,
            confidence: "manual",
            score: 999,
            signals: ["manual_capture"],
          },
        },
        () => {
          // Give scripts time to initialize, then trigger
          setTimeout(async () => {
            try {
              await chrome.scripting.executeScript({
                target: { tabId: currentTabId },
                func: () => {
                  document.dispatchEvent(new CustomEvent("pce-manual-capture"));
                },
              });
            } catch {}
            capturePageBtn.textContent = "Captured!";
            setTimeout(() => {
              capturePageBtn.textContent = "Capture This Page";
              capturePageBtn.disabled = false;
            }, 2000);
          }, 2000);
        }
      );
      return;
    }

    capturePageBtn.textContent = "Captured!";
    setTimeout(() => {
      capturePageBtn.textContent = "Capture This Page";
      capturePageBtn.disabled = false;
    }, 2000);
  } catch (err) {
    capturePageBtn.textContent = "Failed";
    setTimeout(() => {
      capturePageBtn.textContent = "Capture This Page";
      capturePageBtn.disabled = false;
    }, 2000);
  }
});

// ---------------------------------------------------------------------------
// 5. Blacklist management
// ---------------------------------------------------------------------------
async function loadBlacklist() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["pce_blacklist"], (result) => {
      currentBlacklist = result.pce_blacklist || [];
      resolve();
    });
  });
}

async function saveBlacklist(list) {
  return new Promise((resolve) => {
    chrome.storage.local.set({ pce_blacklist: list }, resolve);
  });
}

blacklistBtn.addEventListener("click", async () => {
  if (!currentDomain) return;
  await loadBlacklist();
  if (!currentBlacklist.includes(currentDomain)) {
    currentBlacklist.push(currentDomain);
    await saveBlacklist(currentBlacklist);
  }
  // Notify service worker
  chrome.runtime.sendMessage({
    type: "PCE_BLACKLIST_UPDATED",
    blacklist: currentBlacklist,
  });
  setPageStatus("blocked", currentDomain, "Blocked by you");
  blacklistBtn.style.display = "none";
  unblacklistBtn.style.display = "";
  capturePageBtn.disabled = true;
});

unblacklistBtn.addEventListener("click", async () => {
  if (!currentDomain) return;
  await loadBlacklist();
  currentBlacklist = currentBlacklist.filter((d) => d !== currentDomain);
  await saveBlacklist(currentBlacklist);
  chrome.runtime.sendMessage({
    type: "PCE_BLACKLIST_UPDATED",
    blacklist: currentBlacklist,
  });
  unblacklistBtn.style.display = "none";
  // Re-check page status
  checkCurrentPage();
});
