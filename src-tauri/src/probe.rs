use crate::tools::{self, ToolStatus};
use serde::Serialize;
use serde_json::Value;
use std::process::Command;

/// Something about the file the user should know before relying on it.
///
/// `Blocker` means the stream will not work as-is; `Warning` means it will
/// work but not well; `Note` is informational.
#[derive(Serialize, Clone, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Blocker,
    Warning,
    Note,
}

#[derive(Serialize, Clone)]
pub struct Finding {
    pub severity: Severity,
    pub title: String,
    pub detail: String,
}

#[derive(Serialize, Clone)]
pub struct VideoInfo {
    pub path: String,
    pub name: String,
    pub duration: f64,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    pub video_codec: String,
    pub audio_codec: Option<String>,
    pub bitrate_kbps: u32,
    /// Average seconds between keyframes over the sampled window, when measurable.
    pub keyframe_interval: Option<f64>,
    /// True when the file can be streamed with `-c copy`, i.e. no re-encoding.
    pub can_copy: bool,
    pub findings: Vec<Finding>,
}

fn as_f64(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str()?.parse().ok())
}

fn as_u64(v: &Value) -> Option<u64> {
    v.as_u64().or_else(|| v.as_str()?.parse().ok())
}

/// ffprobe reports frame rate as a rational string like "30000/1001".
fn parse_rate(s: &str) -> Option<f64> {
    let (num, den) = s.split_once('/')?;
    let (num, den): (f64, f64) = (num.parse().ok()?, den.parse().ok()?);
    if den == 0.0 {
        return None;
    }
    Some(num / den)
}

/// Average gap between keyframes across the first 60 seconds.
///
/// Sampled rather than exhaustive: a full scan of a two-hour file would take
/// far longer than the user is willing to wait, and the opening minute is
/// representative of how the file was encoded.
fn keyframe_interval(ffprobe: &std::path::Path, path: &str) -> Option<f64> {
    let out = Command::new(ffprobe)
        .args([
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-skip_frame",
            "nokey",
            "-show_entries",
            "frame=pts_time",
            "-read_intervals",
            "%+60",
            "-print_format",
            "json",
        ])
        .arg(path)
        .output()
        .ok()?;

    let json: Value = serde_json::from_slice(&out.stdout).ok()?;
    let times: Vec<f64> = json
        .get("frames")?
        .as_array()?
        .iter()
        .filter_map(|f| as_f64(f.get("pts_time")?))
        .collect();

    if times.len() < 2 {
        return None;
    }
    let gaps: Vec<f64> = times.windows(2).map(|w| w[1] - w[0]).collect();
    Some(gaps.iter().sum::<f64>() / gaps.len() as f64)
}

fn assess(info: &mut VideoInfo) {
    let mut findings = Vec::new();
    let mut copyable = true;

    if info.video_codec != "h264" {
        copyable = false;
        findings.push(Finding {
            severity: Severity::Warning,
            title: format!("Video is {}, not H.264", info.video_codec.to_uppercase()),
            detail: "Platforms expect H.264, so this file has to be re-encoded before it can be streamed. That costs CPU for as long as the stream runs. Converting it once, up front, is cheaper.".into(),
        });
    }

    match info.audio_codec.as_deref() {
        None => {
            copyable = false;
            findings.push(Finding {
                severity: Severity::Blocker,
                title: "No audio track".into(),
                detail: "YouTube and most platforms reject a live stream with no audio. Add a silent track if the video is meant to be silent.".into(),
            });
        }
        Some(codec) if codec != "aac" => {
            copyable = false;
            findings.push(Finding {
                severity: Severity::Warning,
                title: format!("Audio is {}, not AAC", codec.to_uppercase()),
                detail: "Audio has to be re-encoded to AAC for RTMP. Cheap compared to video, but still worth fixing in the file.".into(),
            });
        }
        _ => {}
    }

    match info.keyframe_interval {
        Some(gap) if gap > 4.5 => {
            findings.push(Finding {
                severity: Severity::Warning,
                title: format!("Keyframes every {:.1}s", gap),
                detail: "Platforms want a keyframe roughly every 2 seconds. Longer gaps make viewers wait to see anything when they join, and some platforms reject the stream outright.".into(),
            });
        }
        None => findings.push(Finding {
            severity: Severity::Note,
            title: "Keyframe spacing could not be measured".into(),
            detail: "Not a problem by itself, but if the stream misbehaves this is worth checking."
                .into(),
        }),
        _ => {}
    }

    if info.width % 2 != 0 || info.height % 2 != 0 {
        copyable = false;
        findings.push(Finding {
            severity: Severity::Blocker,
            title: "Odd frame dimensions".into(),
            detail: format!(
                "{}×{} has an odd side. H.264 needs even dimensions, so this file cannot be streamed without resizing.",
                info.width, info.height
            ),
        });
    }

    if info.bitrate_kbps > 12_000 {
        findings.push(Finding {
            severity: Severity::Warning,
            title: format!("High bitrate: {:.1} Mbps", info.bitrate_kbps as f64 / 1000.0),
            detail: "Every destination sends a full copy of this, so the upload cost multiplies with each account. Consider re-encoding lower if the uplink is tight.".into(),
        });
    }

    if info.duration < 5.0 {
        findings.push(Finding {
            severity: Severity::Note,
            title: "Very short file".into(),
            detail: "It will loop every few seconds, which is fine but worth confirming it is what you meant.".into(),
        });
    }

    if copyable {
        findings.push(Finding {
            severity: Severity::Note,
            title: "Ready to stream as-is".into(),
            detail: "H.264 and AAC already, so it streams without re-encoding — almost no CPU cost, however many accounts use it.".into(),
        });
    }

    info.can_copy = copyable;
    info.findings = findings;
}

#[tauri::command]
pub fn probe_video(path: String) -> Result<VideoInfo, String> {
    let tools =
        match tools::resolve() {
            ToolStatus::Ready(t) => t,
            ToolStatus::Missing { .. } => return Err(
                "ffmpeg was not found. Run ./setup.sh, or install it with `brew install ffmpeg`."
                    .into(),
            ),
        };

    if !std::path::Path::new(&path).is_file() {
        return Err(format!("No file at {path}"));
    }

    let out = Command::new(&tools.ffprobe)
        .args([
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
        ])
        .arg(&path)
        .output()
        .map_err(|e| format!("Could not run ffprobe: {e}"))?;

    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "ffprobe could not read this file. {}",
            err.lines().next().unwrap_or("").trim()
        ));
    }

    let json: Value = serde_json::from_slice(&out.stdout)
        .map_err(|e| format!("Unreadable ffprobe output: {e}"))?;
    let streams = json
        .get("streams")
        .and_then(Value::as_array)
        .ok_or("ffprobe reported no streams")?;

    let video = streams
        .iter()
        .find(|s| s.get("codec_type").and_then(Value::as_str) == Some("video"))
        .ok_or("This file has no video track")?;
    let audio = streams
        .iter()
        .find(|s| s.get("codec_type").and_then(Value::as_str) == Some("audio"));

    let format = json.get("format").cloned().unwrap_or(Value::Null);

    // Container bitrate is the honest number: it is what actually goes out.
    let bitrate_kbps = format
        .get("bit_rate")
        .and_then(as_u64)
        .or_else(|| video.get("bit_rate").and_then(as_u64))
        .map(|b| (b / 1000) as u32)
        .unwrap_or(0);

    let mut info = VideoInfo {
        name: std::path::Path::new(&path)
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| path.clone()),
        duration: format.get("duration").and_then(as_f64).unwrap_or(0.0),
        width: video.get("width").and_then(as_u64).unwrap_or(0) as u32,
        height: video.get("height").and_then(as_u64).unwrap_or(0) as u32,
        fps: video
            .get("r_frame_rate")
            .and_then(Value::as_str)
            .and_then(parse_rate)
            .unwrap_or(0.0),
        video_codec: video
            .get("codec_name")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string(),
        audio_codec: audio
            .and_then(|a| a.get("codec_name"))
            .and_then(Value::as_str)
            .map(str::to_string),
        bitrate_kbps,
        keyframe_interval: keyframe_interval(&tools.ffprobe, &path),
        can_copy: true,
        findings: Vec::new(),
        path,
    };

    assess(&mut info);
    Ok(info)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_rate_reads_ffprobe_rationals() {
        assert_eq!(parse_rate("30/1"), Some(30.0));
        assert_eq!(
            parse_rate("30000/1001").map(|v| (v * 100.0).round()),
            Some(2997.0)
        );
        // A still image reports 0/0; dividing would panic or produce NaN.
        assert_eq!(parse_rate("0/0"), None);
        assert_eq!(parse_rate("nonsense"), None);
    }

    fn info() -> VideoInfo {
        VideoInfo {
            path: "/v/a.mp4".into(),
            name: "a.mp4".into(),
            duration: 600.0,
            width: 1920,
            height: 1080,
            fps: 30.0,
            video_codec: "h264".into(),
            audio_codec: Some("aac".into()),
            bitrate_kbps: 4000,
            keyframe_interval: Some(2.0),
            can_copy: true,
            findings: vec![],
        }
    }

    #[test]
    fn a_stream_ready_file_needs_no_re_encoding() {
        let mut v = info();
        assess(&mut v);
        assert!(v.can_copy);
        assert!(v.findings.iter().all(|f| f.severity == Severity::Note));
    }

    #[test]
    fn missing_audio_blocks() {
        let mut v = info();
        v.audio_codec = None;
        assess(&mut v);
        assert!(!v.can_copy);
        assert!(v.findings.iter().any(|f| f.severity == Severity::Blocker));
    }

    #[test]
    fn non_h264_forces_a_re_encode() {
        let mut v = info();
        v.video_codec = "prores".into();
        assess(&mut v);
        assert!(!v.can_copy);
    }

    #[test]
    fn sparse_keyframes_warn_but_still_copy() {
        let mut v = info();
        v.keyframe_interval = Some(10.0);
        assess(&mut v);
        assert!(v.can_copy, "keyframe spacing does not prevent remuxing");
        assert!(v
            .findings
            .iter()
            .any(|f| f.severity == Severity::Warning && f.title.contains("Keyframes")));
    }

    #[test]
    fn odd_dimensions_block() {
        let mut v = info();
        v.width = 1921;
        assess(&mut v);
        assert!(!v.can_copy);
    }
}
