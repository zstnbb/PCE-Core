//! Native system tray — Tauri v2 equivalent of the pystray menu in
//! `pce_app/tray.py`.
//!
//! The menu is built once at setup and mutated by emitting events to the
//! WebView (e.g. toggle Phoenix → `phoenix-toggled`). Every action calls
//! into one of the commands defined in `crate::commands` so logic lives
//! in exactly one place.

use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem, Submenu},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    App, AppHandle, Manager, Runtime,
};

/// Build and attach the tray. Called from `lib.rs`'s setup hook.
pub fn build<R: Runtime>(app: &App<R>) -> tauri::Result<()> {
    let handle = app.handle().clone();

    let open_dashboard = MenuItem::with_id(
        &handle, "open-dashboard", "Open Dashboard", true, None::<&str>,
    )?;
    let run_wizard = MenuItem::with_id(
        &handle, "run-wizard", "Run Setup Wizard", true, None::<&str>,
    )?;
    let diagnostics = MenuItem::with_id(
        &handle, "collect-diagnostics", "Collect Diagnostics…", true, None::<&str>,
    )?;
    let phoenix_toggle = MenuItem::with_id(
        &handle, "phoenix-toggle", "Phoenix: Start / Stop", true, None::<&str>,
    )?;
    let phoenix_open = MenuItem::with_id(
        &handle, "phoenix-open", "Phoenix: Open UI", true, None::<&str>,
    )?;
    let phoenix_submenu = Submenu::with_id_and_items(
        &handle, "phoenix", "Phoenix",
        true,
        &[&phoenix_toggle, &phoenix_open],
    )?;
    let check_updates = MenuItem::with_id(
        &handle, "check-updates", "Check for Updates…", true, None::<&str>,
    )?;
    let restart_core = MenuItem::with_id(
        &handle, "restart-core", "Restart Core Server", true, None::<&str>,
    )?;
    let quit = MenuItem::with_id(
        &handle, "quit", "Quit PCE", true, None::<&str>,
    )?;

    let menu = Menu::with_items(
        &handle,
        &[
            &open_dashboard,
            &run_wizard,
            &PredefinedMenuItem::separator(&handle)?,
            &phoenix_submenu,
            &diagnostics,
            &check_updates,
            &PredefinedMenuItem::separator(&handle)?,
            &restart_core,
            &quit,
        ],
    )?;

    let icon = tray_icon_image(&handle);
    let mut builder = TrayIconBuilder::with_id("pce-tray")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .tooltip("PCE – Personal Cognitive Engine");

    if let Some(img) = icon {
        builder = builder.icon(img);
    }

    builder
        .on_menu_event(move |app, event| {
            handle_menu_event(app.clone(), event.id.as_ref());
        })
        .on_tray_icon_event(|icon, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                focus_main_window(icon.app_handle());
            }
        })
        .build(app)?;

    Ok(())
}

/// Route a menu id to its Tauri command equivalent. We invoke commands
/// asynchronously through the tokio runtime so the tray click handler
/// returns immediately — otherwise a slow network call (e.g. update
/// check) would freeze the menu.
fn handle_menu_event<R: Runtime>(app: AppHandle<R>, id: &str) {
    let id = id.to_string();
    tauri::async_runtime::spawn(async move {
        let result = match id.as_str() {
            "open-dashboard" => run_cmd(&app, "open_dashboard").await,
            "run-wizard"     => run_cmd(&app, "open_onboarding").await,
            "collect-diagnostics" => run_cmd(&app, "collect_diagnostics").await,
            "phoenix-toggle" => run_cmd(&app, "phoenix_toggle").await,
            "phoenix-open"   => run_cmd(&app, "open_phoenix_ui").await,
            "check-updates"  => run_cmd(&app, "check_for_updates").await,
            "restart-core"   => run_cmd(&app, "restart_core").await,
            "quit" => {
                app.exit(0);
                return;
            }
            other => {
                log::warn!("unknown tray menu id: {other}");
                return;
            }
        };

        if let Some(payload) = result {
            // Let the dashboard listen for these if it wants to render toasts.
            let _ = app.emit("tray-action-result", payload);
        }
    });
}

/// Forward a tray click to the matching Tauri command by building a small
/// JSON payload and letting the generated dispatcher run it. We wrap the
/// command result (or error) into the same CommandResult shape.
async fn run_cmd<R: Runtime>(app: &AppHandle<R>, name: &str) -> Option<serde_json::Value> {
    use crate::commands::{
        check_for_updates, collect_diagnostics, open_dashboard, open_onboarding,
        open_phoenix_ui, phoenix_toggle, reset_onboarding, restart_core,
    };
    use crate::AppState;

    let state = app.state::<AppState>();
    let result = match name {
        "open_dashboard" => open_dashboard(app.clone(), state.clone()).await,
        "open_onboarding" => open_onboarding(app.clone(), state.clone()).await,
        "open_phoenix_ui" => open_phoenix_ui(app.clone()).await,
        "collect_diagnostics" => collect_diagnostics(state.clone()).await,
        "phoenix_toggle" => phoenix_toggle(state.clone()).await,
        "check_for_updates" => check_for_updates(state.clone()).await,
        "reset_onboarding" => reset_onboarding(state.clone()).await,
        "restart_core" => restart_core(state.clone()).await,
        _ => return None,
    };
    match result {
        Ok(r) => serde_json::to_value(r).ok(),
        Err(err) => {
            log::warn!("tray command {name} failed: {err}");
            Some(serde_json::json!({
                "ok": false,
                "title": "PCE",
                "message": format!("{name} failed: {err}"),
            }))
        }
    }
}

fn focus_main_window<R: Runtime>(app: &AppHandle<R>) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn tray_icon_image<R: Runtime>(app: &AppHandle<R>) -> Option<Image<'static>> {
    // Prefer the default-window icon so the tray stays consistent with
    // the app identity. If the icon file is missing (e.g. on first
    // checkout before icons/ has been populated) we just skip it — the
    // OS will fall back to a blank square.
    app.default_window_icon().cloned()
}
