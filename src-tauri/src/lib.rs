mod probe;
mod stream;
mod tools;

use serde::Serialize;

#[cfg(debug_assertions)]
const PROFILE: &str = "debug";
#[cfg(not(debug_assertions))]
const PROFILE: &str = "release";

/// Facts about the running binary, surfaced in the UI's status bar.
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
        .plugin(tauri_plugin_dialog::init())
        .manage(stream::Supervisor::default())
        .invoke_handler(tauri::generate_handler![
            build_info,
            tools::tool_status,
            probe::probe_video,
            stream::start_loop,
            stream::stop_loop,
            stream::running_loops,
        ])
        .run(tauri::generate_context!())
        .expect("error while running StreamBridge");
}
