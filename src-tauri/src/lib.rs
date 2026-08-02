mod diagnose;
mod netmeter;
mod probe;
mod release;
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

/// The version the app actually shipped as.
///
/// Deliberately not `CARGO_PKG_VERSION`. tauri-codegen prefers the version in
/// tauri.conf.json and only falls back to Cargo's, so the two can disagree —
/// and when they did, this reported 0.1.0 for a bundle labelled 0.2.1. Reading
/// what Tauri resolved means this figure, the bundle, and the updater's own
/// comparison can never tell three different stories.
pub fn version(app: &tauri::AppHandle) -> String {
    app.package_info().version.to_string()
}

#[tauri::command]
fn build_info(app: tauri::AppHandle) -> BuildInfo {
    BuildInfo {
        name: env!("CARGO_PKG_NAME").to_string(),
        version: version(&app),
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        profile: PROFILE.to_string(),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(stream::Supervisor::default())
        .setup(|app| {
            // A crash or force-quit cannot run cleanup, so the next launch is
            // the only chance to kill a publisher still uploading to a platform
            // that believes the stream is live.
            let handle = app.handle().clone();
            let reaped = handle.state::<stream::Supervisor>().reap_orphans(&handle);
            if !reaped.is_empty() {
                eprintln!(
                    "Caudal: stopped {} publisher(s) left over from a previous run",
                    reaped.len()
                );
            }

            // Measure the uplink for as long as the app is open, not only while
            // streaming: knowing what else is using the connection is most
            // useful *before* deciding to add another destination.
            netmeter::spawn(handle.clone());
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
            release::updater_signed,
            release::latest_release,
            release::open_release,
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
        .expect("error while building Caudal")
        .run(|app, event| {
            // Quitting must take the streams with it. Left alone, each ffmpeg
            // would carry on uploading with nothing able to stop it short of
            // finding the process by hand.
            if let tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit = event {
                app.state::<stream::Supervisor>().shutdown();
            }
        });
}

#[cfg(test)]
mod tests {
    /// The two version numbers must agree.
    ///
    /// They are separate files and nothing forced them to match, so they drifted:
    /// tauri.conf.json reached 0.2.1 while Cargo.toml sat at 0.1.0, and the app
    /// shipped as 0.2.1 while reporting 0.1.0 in its own status bar. The update
    /// check compared against the wrong one, which would have offered an update
    /// to a copy already running the newest build — for ever, since installing it
    /// changed nothing.
    ///
    /// Cheap to assert, and it fails on the machine of whoever forgot rather than
    /// in the hands of whoever installed.
    #[test]
    fn the_two_declared_versions_agree() {
        let conf: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(
            conf["version"].as_str().unwrap(),
            env!("CARGO_PKG_VERSION"),
            "tauri.conf.json and Cargo.toml disagree about the version; \
             tauri-codegen prefers the former, so Cargo's would be reported \
             by anything reading CARGO_PKG_VERSION"
        );
    }
}
