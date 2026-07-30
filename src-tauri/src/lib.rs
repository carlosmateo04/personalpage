use serde::Serialize;

#[cfg(debug_assertions)]
const PROFILE: &str = "debug";
#[cfg(not(debug_assertions))]
const PROFILE: &str = "release";

/// Facts about the running binary, surfaced in the UI so that a successful
/// render proves both that the bundle was assembled correctly and that the
/// frontend can reach the Rust backend over the IPC bridge.
#[derive(Serialize)]
pub struct BuildInfo {
    name: String,
    version: String,
    os: String,
    arch: String,
    profile: String,
}

#[tauri::command]
fn build_info() -> BuildInfo {
    BuildInfo {
        name: env!("CARGO_PKG_NAME").to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        profile: PROFILE.to_string(),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![build_info])
        .run(tauri::generate_context!())
        .expect("error while running StreamBridge");
}
