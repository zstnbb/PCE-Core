//! PCE Desktop shell — Tauri v2 entry.
//!
//! Responsibilities (kept intentionally thin — the Python core does the work):
//!
//! 1. Spawn `python -m pce_app --no-tray --no-browser` as a sidecar so the
//!    FastAPI server + normalizers + SQLite all run exactly like they do
//!    in dev mode. When the shell quits, the sidecar gets terminated.
//! 2. Poll `/api/v1/health` until the server is ready, then show the
//!    main WebView window pointing at `http://127.0.0.1:9800/` (which
//!    auto-redirects to `/onboarding` on first run — no Rust work needed).
//! 3. Build a native tray with Setup Wizard / Collect Diagnostics /
//!    Phoenix Start-Stop / Check for Updates / Quit, each implemented as
//!    a Tauri IPC command that ultimately calls the same HTTP endpoints
//!    the pystray tray uses (see `pce_app/tray_actions.py`).

mod commands;
mod sidecar;
mod tray;

use std::sync::Arc;

use tauri::{
    Manager, RunEvent, WebviewUrl, WebviewWindowBuilder,
};
use tokio::sync::Mutex;

use crate::sidecar::Sidecar;

/// App-wide state shared across Tauri commands. Wrapped in `Mutex` so
/// lifecycle actions (start/stop/restart) can serialize access to the
/// child process handle without blocking the UI thread.
pub struct AppState {
    pub sidecar: Arc<Mutex<Sidecar>>,
    pub core_url: String,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let core_url = "http://127.0.0.1:9800".to_string();
    let sidecar = Arc::new(Mutex::new(Sidecar::new()));
    let state = AppState {
        sidecar: sidecar.clone(),
        core_url: core_url.clone(),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // If the user double-clicks the app again, surface the existing window.
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.unminimize();
                let _ = win.show();
                let _ = win.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            commands::open_dashboard,
            commands::open_onboarding,
            commands::open_phoenix_ui,
            commands::collect_diagnostics,
            commands::phoenix_toggle,
            commands::check_for_updates,
            commands::reset_onboarding,
            commands::restart_core,
            commands::core_health,
            commands::quit_app,
        ])
        .setup(move |app| {
            // Kick off the Python core so the front-end has something to point at.
            let handle = app.handle().clone();
            let core_url_clone = core_url.clone();
            let sidecar_clone = sidecar.clone();

            tauri::async_runtime::spawn(async move {
                let mut side = sidecar_clone.lock().await;
                if let Err(err) = side.start() {
                    log::error!("sidecar start failed: {err}");
                    let _ = handle.dialog_error(
                        "Could not start PCE core",
                        format!(
                            "Failed to launch the Python ingest server: {err}\n\n\
                             You can try running `python -m pce_app --no-tray --no-browser` \
                             manually and restarting PCE Desktop."
                        ),
                    );
                    return;
                }
                drop(side);

                // Poll /api/v1/health until we see it come up (15s budget).
                let ready = sidecar::wait_for_core(&core_url_clone, 15_000).await;
                if ready {
                    log::info!("core server is ready at {core_url_clone}");
                } else {
                    log::warn!(
                        "core server did not respond on {} within 15s \
                         — showing window anyway",
                        core_url_clone
                    );
                }

                // Show the window now that the server is (most likely) ready.
                if let Some(win) = handle.get_webview_window("main") {
                    let _ = win.show();
                    let _ = win.set_focus();
                } else {
                    // Window was created hidden in tauri.conf.json — build it.
                    let _ = WebviewWindowBuilder::new(
                        &handle,
                        "main",
                        WebviewUrl::External(core_url_clone.parse().unwrap()),
                    )
                    .title("PCE – Personal Cognitive Engine")
                    .inner_size(1280.0, 840.0)
                    .min_inner_size(960.0, 600.0)
                    .build();
                }
            });

            // Build the native tray.
            tray::build(app)?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::ExitRequested { .. } = event {
                // Block the runtime briefly while we shut the sidecar down so
                // we don't leave a zombie python.exe behind.
                let state = app.state::<AppState>();
                let sidecar = state.sidecar.clone();
                tauri::async_runtime::block_on(async move {
                    let mut side = sidecar.lock().await;
                    side.stop();
                });
            }
        });
}

// Convenience error dialog helper — pulls in tauri-plugin-dialog.
trait DialogExt {
    fn dialog_error(&self, title: impl Into<String>, body: impl Into<String>) -> tauri::Result<()>;
}

impl<R: tauri::Runtime> DialogExt for tauri::AppHandle<R> {
    fn dialog_error(&self, title: impl Into<String>, body: impl Into<String>) -> tauri::Result<()> {
        use tauri_plugin_dialog::{DialogExt as _, MessageDialogKind};
        self.dialog()
            .message(body.into())
            .title(title.into())
            .kind(MessageDialogKind::Error)
            .blocking_show();
        Ok(())
    }
}
