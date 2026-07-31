use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Where the ffmpeg pair came from, so the UI can say why something is missing.
#[derive(Serialize, Clone, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum ToolSource {
    /// Shipped inside the app bundle.
    Bundled,
    /// Found in a Homebrew prefix.
    Homebrew,
    /// Found on PATH.
    System,
}

#[derive(Serialize, Clone)]
pub struct Tools {
    pub ffmpeg: PathBuf,
    pub ffprobe: PathBuf,
    pub source: ToolSource,
    pub version: String,
}

#[derive(Serialize, Clone)]
#[serde(tag = "state", rename_all = "lowercase")]
pub enum ToolStatus {
    Ready(Tools),
    Missing { searched: Vec<String> },
}

fn version_of(ffmpeg: &Path) -> Option<String> {
    let out = Command::new(ffmpeg).arg("-version").output().ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    // "ffmpeg version 7.1 Copyright ..." → "7.1"
    text.lines()
        .next()?
        .split_whitespace()
        .nth(2)
        .map(|s| s.to_string())
}

/// A directory counts only when it holds both binaries and ffmpeg actually runs.
fn try_dir(dir: &Path, source: ToolSource) -> Option<Tools> {
    let ffmpeg = dir.join("ffmpeg");
    let ffprobe = dir.join("ffprobe");
    if !ffmpeg.is_file() || !ffprobe.is_file() {
        return None;
    }
    let version = version_of(&ffmpeg)?;
    Some(Tools {
        ffmpeg,
        ffprobe,
        source,
        version,
    })
}

/// Candidate directories in preference order: bundled first so a shipped copy
/// always wins over whatever the machine happens to have.
fn candidates(exe_dir: Option<&Path>) -> Vec<(PathBuf, ToolSource)> {
    let mut dirs: Vec<(PathBuf, ToolSource)> = Vec::new();

    if let Some(dir) = exe_dir {
        dirs.push((dir.to_path_buf(), ToolSource::Bundled));
        // Tauri sidecars land beside the executable inside Contents/MacOS.
        dirs.push((dir.join("../Resources"), ToolSource::Bundled));
    }

    dirs.push((PathBuf::from("/opt/homebrew/bin"), ToolSource::Homebrew));
    dirs.push((PathBuf::from("/usr/local/bin"), ToolSource::Homebrew));
    dirs.push((PathBuf::from("/usr/bin"), ToolSource::System));

    if let Ok(path) = std::env::var("PATH") {
        for dir in std::env::split_paths(&path) {
            dirs.push((dir, ToolSource::System));
        }
    }
    dirs
}

pub fn resolve() -> ToolStatus {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf));

    let dirs = candidates(exe_dir.as_deref());
    let mut searched = Vec::new();

    for (dir, source) in &dirs {
        if let Some(tools) = try_dir(dir, source.clone()) {
            return ToolStatus::Ready(tools);
        }
        searched.push(dir.display().to_string());
    }

    searched.dedup();
    ToolStatus::Missing { searched }
}

#[tauri::command]
pub fn tool_status() -> ToolStatus {
    resolve()
}
