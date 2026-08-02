//! Persistence for everything that is not a secret.
//!
//! The frontend owns the shape of a destination, so it hands over its own JSON
//! rather than the shape being duplicated here — two definitions of the same
//! record drift apart, and the one that drifts is always the one nobody is
//! looking at. Stream keys never pass through here; they go to the Keychain.

use std::fs;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

const FILE: &str = "destinations.json";

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("No config directory available: {e}"))?;
    fs::create_dir_all(&dir).map_err(|e| format!("Could not create {}: {e}", dir.display()))?;
    Ok(dir.join(FILE))
}

#[tauri::command]
pub fn save_config(app: AppHandle, json: String) -> Result<(), String> {
    // Refuse to persist anything unparseable rather than writing a file that
    // will fail to load later, when the reason is long forgotten.
    serde_json::from_str::<serde_json::Value>(&json)
        .map_err(|e| format!("Refusing to save malformed configuration: {e}"))?;

    let path = config_path(&app)?;
    let tmp = path.with_extension("json.tmp");

    // Write then rename, so a crash mid-write cannot leave a truncated file
    // where a working configuration used to be. For an app expected to run for
    // weeks, the unlucky moment will eventually arrive.
    fs::write(&tmp, json).map_err(|e| format!("Could not write configuration: {e}"))?;
    fs::rename(&tmp, &path).map_err(|e| format!("Could not replace configuration: {e}"))?;
    Ok(())
}

#[tauri::command]
pub fn load_config(app: AppHandle) -> Result<Option<String>, String> {
    let path = config_path(&app)?;
    match fs::read_to_string(&path) {
        Ok(json) => Ok(Some(json)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("Could not read configuration: {e}")),
    }
}

/// Where the configuration lives, for the settings panel and for support.
#[tauri::command]
pub fn config_location(app: AppHandle) -> Result<String, String> {
    Ok(config_path(&app)?.display().to_string())
}
