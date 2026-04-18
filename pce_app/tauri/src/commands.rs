//! Tauri IPC commands exposed to the WebView JavaScript.
//!
//! Each command maps to a user-visible action (tray menu item, settings
//! button, etc.) and mostly forwards to the Python core's HTTP surface.
//! Keeping the glue thin means new actions can ship by just adding a
//! method here + a button in the dashboard — no Rust redeploy required
//! for the Python-side logic changes.

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime, State};
use tauri_plugin_opener::OpenerExt;

use crate::AppState;

#[derive(Debug, Serialize)]
pub struct CommandResult {
    pub ok: bool,
    pub title: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payload: Option<serde_json::Value>,
}

impl CommandResult {
    fn ok<S: Into<String>>(title: S, message: S) -> Self {
        Self {
            ok: true,
            title: title.into(),
            message: message.into(),
            payload: None,
        }
    }

    fn fail<S: Into<String>>(title: S, message: S) -> Self {
        Self {
            ok: false,
            title: title.into(),
            message: message.into(),
            payload: None,
        }
    }

    fn with_payload(mut self, payload: serde_json::Value) -> Self {
        self.payload = Some(payload);
        self
    }
}

// ---------------------------------------------------------------------------
// Window / browser openers
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn open_dashboard<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, AppState>,
) -> Result<CommandResult, String> {
    let url = state.core_url.clone();
    open_external(&app, &url);
    Ok(CommandResult::ok("Dashboard", "Opened in browser."))
}

#[tauri::command]
pub async fn open_onboarding<R: Runtime>(
    app: AppHandle<R>,
    state: State<'_, AppState>,
) -> Result<CommandResult, String> {
    let url = format!("{}/onboarding", state.core_url.trim_end_matches('/'));
    open_external(&app, &url);
    Ok(CommandResult::ok("Setup Wizard", "Opened wizard in browser."))
}

#[tauri::command]
pub async fn open_phoenix_ui<R: Runtime>(app: AppHandle<R>) -> Result<CommandResult, String> {
    open_external(&app, "http://127.0.0.1:6006/");
    Ok(CommandResult::ok("Phoenix", "Opened Phoenix UI."))
}

fn open_external<R: Runtime>(app: &AppHandle<R>, url: &str) {
    if let Err(err) = app.opener().open_url(url, None::<&str>) {
        log::warn!("open_external {url} failed: {err}");
    }
}

// ---------------------------------------------------------------------------
// Core actions (forward to Python ingest server)
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn collect_diagnostics(
    state: State<'_, AppState>,
) -> Result<CommandResult, String> {
    let url = format!("{}/api/v1/diagnose", state.core_url.trim_end_matches('/'));
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            return Ok(CommandResult::fail(
                "Collect Diagnostics",
                format!("HTTP client init failed: {e}").as_str(),
            ));
        }
    };
    match client.get(&url).send().await {
        Ok(resp) if resp.status().is_success() => {
            let bytes = match resp.bytes().await {
                Ok(b) => b,
                Err(e) => {
                    return Ok(CommandResult::fail(
                        "Collect Diagnostics",
                        format!("Read body failed: {e}").as_str(),
                    ));
                }
            };
            // Write to ~/.pce/data/diagnose/pce-diag-<ts>.zip.
            let out_dir = dirs::home_dir()
                .unwrap_or_else(|| std::path::PathBuf::from("."))
                .join(".pce/data/diagnose");
            if let Err(e) = std::fs::create_dir_all(&out_dir) {
                return Ok(CommandResult::fail(
                    "Collect Diagnostics",
                    format!("Could not create {}: {e}", out_dir.display()).as_str(),
                ));
            }
            let ts = chrono_utc_stamp();
            let path = out_dir.join(format!("pce-diag-{ts}.zip"));
            if let Err(e) = std::fs::write(&path, &bytes) {
                return Ok(CommandResult::fail(
                    "Collect Diagnostics",
                    format!("Write failed: {e}").as_str(),
                ));
            }
            Ok(CommandResult::ok(
                "Diagnostics saved",
                format!("Wrote {}", path.display()).as_str(),
            )
            .with_payload(serde_json::json!({
                "path": path,
                "size_bytes": bytes.len(),
            })))
        }
        Ok(resp) => Ok(CommandResult::fail(
            "Collect Diagnostics",
            format!("HTTP {}: {}", resp.status(), url).as_str(),
        )),
        Err(e) => Ok(CommandResult::fail(
            "Collect Diagnostics",
            format!("Core server unreachable: {e}").as_str(),
        )),
    }
}

#[tauri::command]
pub async fn phoenix_toggle(state: State<'_, AppState>) -> Result<CommandResult, String> {
    let base = state.core_url.trim_end_matches('/').to_string();
    let client = reqwest::Client::new();
    let status: serde_json::Value = match client
        .get(format!("{base}/api/v1/phoenix"))
        .send()
        .await
        .and_then(|r| r.error_for_status())
    {
        Ok(r) => r.json().await.unwrap_or(serde_json::json!({})),
        Err(e) => {
            return Ok(CommandResult::fail(
                "Phoenix",
                format!("Core server unreachable: {e}").as_str(),
            ));
        }
    };
    let running = status.get("running").and_then(|v| v.as_bool()).unwrap_or(false);
    let endpoint = if running { "/api/v1/phoenix/stop" } else { "/api/v1/phoenix/start" };
    let resp: serde_json::Value = match client
        .post(format!("{base}{endpoint}"))
        .json(&serde_json::json!({}))
        .send()
        .await
    {
        Ok(r) => r.json().await.unwrap_or(serde_json::json!({})),
        Err(e) => return Ok(CommandResult::fail("Phoenix", format!("{e}").as_str())),
    };

    let ok = resp.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
    if ok {
        let title = if running { "Phoenix stopped" } else { "Phoenix started" };
        let msg = resp
            .get("ui_url")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        Ok(CommandResult::ok(title, msg.as_str()).with_payload(resp))
    } else {
        let err = resp
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("failed");
        Ok(CommandResult::fail("Phoenix", err).with_payload(resp))
    }
}

#[tauri::command]
pub async fn check_for_updates(state: State<'_, AppState>) -> Result<CommandResult, String> {
    let url = format!("{}/api/v1/updates/check", state.core_url.trim_end_matches('/'));
    let client = reqwest::Client::new();
    match client.get(&url).send().await {
        Ok(resp) if resp.status().is_success() => {
            let body: serde_json::Value = resp.json().await.unwrap_or_default();
            let available = body
                .get("update_available")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let latest = body
                .get("latest_version")
                .and_then(|v| v.as_str())
                .unwrap_or("?")
                .to_string();
            let current = body
                .get("current_version")
                .and_then(|v| v.as_str())
                .unwrap_or("?")
                .to_string();
            if available {
                Ok(CommandResult::ok(
                    "Update available",
                    format!("PCE {latest} is available.").as_str(),
                )
                .with_payload(body))
            } else {
                Ok(CommandResult::ok(
                    "Up to date",
                    format!("PCE {current} is the latest.").as_str(),
                )
                .with_payload(body))
            }
        }
        Ok(resp) => Ok(CommandResult::fail(
            "Check for updates",
            format!("HTTP {}", resp.status()).as_str(),
        )),
        Err(e) => Ok(CommandResult::fail(
            "Check for updates",
            format!("Could not reach update manifest: {e}").as_str(),
        )),
    }
}

#[tauri::command]
pub async fn reset_onboarding(state: State<'_, AppState>) -> Result<CommandResult, String> {
    let url = format!(
        "{}/api/v1/onboarding/reset",
        state.core_url.trim_end_matches('/')
    );
    let client = reqwest::Client::new();
    match client.post(&url).send().await {
        Ok(resp) if resp.status().is_success() => Ok(CommandResult::ok(
            "Setup Wizard",
            "Setup wizard will reopen on next launch.",
        )),
        Ok(resp) => Ok(CommandResult::fail(
            "Setup Wizard",
            format!("HTTP {}", resp.status()).as_str(),
        )),
        Err(e) => Ok(CommandResult::fail(
            "Setup Wizard",
            format!("Reset failed: {e}").as_str(),
        )),
    }
}

#[tauri::command]
pub async fn restart_core(state: State<'_, AppState>) -> Result<CommandResult, String> {
    let mut side = state.sidecar.lock().await;
    if let Err(err) = side.restart() {
        return Ok(CommandResult::fail(
            "Restart core",
            format!("Restart failed: {err}").as_str(),
        ));
    }
    drop(side);
    let ready = crate::sidecar::wait_for_core(&state.core_url, 15_000).await;
    if ready {
        Ok(CommandResult::ok("Core restarted", "Server is ready again."))
    } else {
        Ok(CommandResult::fail(
            "Core restart",
            "Process restarted but health check did not respond within 15 s.",
        ))
    }
}

#[tauri::command]
pub async fn core_health(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let url = format!("{}/api/v1/health", state.core_url.trim_end_matches('/'));
    match reqwest::get(&url).await {
        Ok(resp) => resp.json().await.map_err(|e| e.to_string()),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
pub async fn quit_app<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    app.exit(0);
    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Lightweight UTC timestamp without pulling in `chrono`. Format:
/// `YYYYmmdd-HHMMSS`.
fn chrono_utc_stamp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Break apart without pulling in chrono. Good enough for a filename.
    let secs = epoch % 60;
    let mins = (epoch / 60) % 60;
    let hours = (epoch / 3600) % 24;
    let days_since_epoch = epoch / 86_400;
    let (y, m, d) = civil_from_days(days_since_epoch as i64);
    format!(
        "{:04}{:02}{:02}-{:02}{:02}{:02}",
        y, m, d, hours, mins, secs,
    )
}

/// Howard Hinnant's algorithm — convert days-since-1970 into a civil
/// (year, month, day) tuple. Avoids the chrono dep in the hot path.
fn civil_from_days(days: i64) -> (i32, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if m <= 2 { y + 1 } else { y };
    (year as i32, m as u32, d as u32)
}
