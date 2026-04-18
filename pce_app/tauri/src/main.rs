// Prevents additional console window on Windows in release builds; the
// tray is our UI surface, not a terminal.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("info"),
    ).init();

    pce_desktop_lib::run();
}
