mod diagnose;
mod probe;
mod secrets;
mod store;
mod stream;
mod tools;

use serde::Serialize;
use tauri::Manager;

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
        .setup(|app| {
            // A crash or force-quit cannot run cleanup, so the next launch is
            // the only chance to kill a publisher still uploading to a platform
            // that believes the stream is live.
            let handle = app.handle().clone();
            let reaped = handle.state::<stream::Supervisor>().reap_orphans(&handle);
            if !reaped.is_empty() {
                eprintln!(
                    "StreamBridge: stopped {} publisher(s) left over from a previous run",
                    reaped.len()
                );
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            build_info,
            tools::tool_status,
            probe::probe_video,
            stream::start_loop,
            stream::stop_loop,
            stream::running_loops,
            stream::stop_all,
            stream::incidents,
            stream::keeping_awake,
            secrets::secret_store,
            secrets::set_secret,
            secrets::get_secret,
            secrets::delete_secret,
            secrets::which_have_secrets,
            store::save_config,
            store::load_config,
            store::config_location,
        ])
        .build(tauri::generate_context!())
        .expect("error while building StreamBridge")
        .run(|app, event| {
            // Quitting must take the streams with it. Left alone, each ffmpeg
            // would carry on uploading with nothing able to stop it short of
            // finding the process by hand.
            if let tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit = event {
                app.state::<stream::Supervisor>().shutdown();
            }
        });
}
