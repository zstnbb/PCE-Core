//! Python core supervision.
//!
//! We intentionally keep this small: it's a thin wrapper around
//! [`std::process::Child`] with helpers to start, stop and health-probe
//! the Python ingest server. The server itself is `python -m pce_app
//! --no-tray --no-browser` so every runtime guarantee the Python stack
//! already makes (retention, normalization, supervisor…) still applies.
//!
//! Discovery order for the Python binary:
//!
//! 1. `PCE_PYTHON` env var (explicit override).
//! 2. `../venv/Scripts/python.exe` or `../venv/bin/python` adjacent to
//!    the Tauri project (developer convenience).
//! 3. `python` on PATH.
//!
//! For packaged builds we prefer the bundled `binaries/pce-core(.exe)`
//! sidecar declared in `tauri.conf.json` — see `resolve_bundled_sidecar`.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// Windows CREATE_NO_WINDOW flag so the Python console doesn't pop up
/// every time we start the sidecar.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub struct Sidecar {
    child: Option<Child>,
}

impl Sidecar {
    pub fn new() -> Self {
        Self { child: None }
    }

    pub fn is_running(&mut self) -> bool {
        match self.child.as_mut() {
            None => false,
            Some(ch) => matches!(ch.try_wait(), Ok(None)),
        }
    }

    /// Spawn the Python core. Idempotent: if we already have a live child
    /// we just return `Ok`.
    pub fn start(&mut self) -> Result<(), String> {
        if self.is_running() {
            return Ok(());
        }

        let (program, args) = resolve_invocation()?;
        let mut cmd = Command::new(&program);
        cmd.args(args);
        cmd.stdout(Stdio::null());
        cmd.stderr(Stdio::null());
        cmd.stdin(Stdio::null());
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);

        log::info!(
            "starting sidecar: {} {}",
            program.to_string_lossy(),
            std::env::args().skip(1).collect::<Vec<_>>().join(" "),
        );

        let child = cmd
            .spawn()
            .map_err(|e| format!("spawn({}) failed: {e}", program.to_string_lossy()))?;
        self.child = Some(child);
        Ok(())
    }

    /// Best-effort graceful shutdown. Falls back to `kill` after 5 seconds.
    pub fn stop(&mut self) {
        let Some(mut child) = self.child.take() else {
            return;
        };
        // On Unix we'd send SIGTERM; on Windows there is no SIGTERM, but
        // terminate() / kill() are equivalent here. FastAPI's lifespan
        // handlers still run for a brief moment either way.
        if let Err(err) = child.kill() {
            log::warn!("sidecar.kill failed: {err}");
            return;
        }
        let _ = child.wait();
        log::info!("sidecar stopped");
    }

    pub fn restart(&mut self) -> Result<(), String> {
        self.stop();
        self.start()
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Block up to `timeout_ms` waiting for `/api/v1/health` to respond 200.
pub async fn wait_for_core(base_url: &str, timeout_ms: u64) -> bool {
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_millis(1_500))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            log::warn!("wait_for_core: reqwest client build failed: {e}");
            return false;
        }
    };
    let deadline = std::time::Instant::now() + Duration::from_millis(timeout_ms);
    let url = format!("{}/api/v1/health", base_url.trim_end_matches('/'));
    while std::time::Instant::now() < deadline {
        match client.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => return true,
            _ => tokio::time::sleep(Duration::from_millis(250)).await,
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

fn resolve_invocation() -> Result<(PathBuf, Vec<OsString>), String> {
    if let Some(bundled) = resolve_bundled_sidecar() {
        // `externalBin` ships a pre-built binary; it takes its own args.
        return Ok((
            bundled,
            vec!["--no-tray".into(), "--no-browser".into()],
        ));
    }

    let python = resolve_python_binary()?;
    Ok((
        python,
        vec![
            "-m".into(),
            "pce_app".into(),
            "--no-tray".into(),
            "--no-browser".into(),
        ],
    ))
}

/// Returns the path to a bundled sidecar if the installer placed one next
/// to the Tauri binary (see `bundle.externalBin` in `tauri.conf.json`).
fn resolve_bundled_sidecar() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    #[cfg(windows)]
    let candidates = ["pce-core.exe"];
    #[cfg(not(windows))]
    let candidates = ["pce-core"];
    for name in candidates {
        let p = dir.join(name);
        if p.is_file() {
            return Some(p);
        }
    }
    None
}

fn resolve_python_binary() -> Result<PathBuf, String> {
    if let Some(explicit) = std::env::var_os("PCE_PYTHON") {
        let p = PathBuf::from(explicit);
        if p.is_file() {
            return Ok(p);
        }
    }

    // Dev convenience: look for ../venv/ relative to the tauri crate.
    if let Some(manifest_dir) = std::env::var_os("CARGO_MANIFEST_DIR") {
        let root = PathBuf::from(manifest_dir);
        for subpath in [
            "venv/Scripts/python.exe",
            "venv/bin/python",
            "../../venv/Scripts/python.exe",
            "../../venv/bin/python",
        ] {
            let cand = root.join(subpath);
            if cand.is_file() {
                return Ok(cand);
            }
        }
    }

    // Fallback: `python` on PATH. We return the literal name and rely on
    // OS path resolution. Spawn-time failure will surface a clear error.
    Ok(PathBuf::from(if cfg!(windows) { "python.exe" } else { "python" }))
}
