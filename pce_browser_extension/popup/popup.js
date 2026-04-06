/**
 * PCE Browser Extension – Popup script
 */

const serverStatus = document.getElementById("server-status");
const captureCount = document.getElementById("capture-count");
const enabledToggle = document.getElementById("enabled-toggle");
const lastError = document.getElementById("last-error");
const dashboardLink = document.getElementById("dashboard-link");

dashboardLink.href = "http://127.0.0.1:9800/docs";

// Get current status from background
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
});

// Toggle handler
enabledToggle.addEventListener("change", () => {
  chrome.runtime.sendMessage(
    { type: "PCE_SET_ENABLED", enabled: enabledToggle.checked },
    (response) => {
      if (chrome.runtime.lastError) return;
    }
  );
});
