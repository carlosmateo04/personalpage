use crate::tools::{self, ToolStatus};
use serde::Serialize;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager};

/// One progress sample, emitted roughly once a second while a loop runs.
#[derive(Serialize, Clone, Default)]
pub struct Progress {
    pub id: String,
    pub frames: u64,
    pub fps: f64,
    pub bitrate_kbps: f64,
    pub total_bytes: u64,
    pub out_time_s: f64,
    pub dropped: u64,
    pub duplicated: u64,
    /// Encoding speed relative to real time. Below 1.0 means falling behind.
    pub speed: f64,
}

#[derive(Serialize, Clone)]
pub struct Ended {
    pub id: String,
    pub code: Option<i32>,
    /// True when the process was stopped on purpose rather than dying.
    pub deliberate: bool,
    pub error: Option<String>,
}

struct Running {
    child: Child,
    stdin: Option<ChildStdin>,
    /// Set before stopping, so the exit handler can tell a deliberate stop from a crash.
    deliberate: Arc<Mutex<bool>>,
}

#[derive(Default)]
pub struct Supervisor {
    running: Mutex<HashMap<String, Running>>,
}

/// ffmpeg reports values like "1234.5kbits/s", "1.02x", or "N/A".
fn numeric_prefix(raw: &str) -> f64 {
    let cleaned: String = raw
        .trim()
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.' || *c == '-')
        .collect();
    cleaned.parse().unwrap_or(0.0)
}

fn apply(progress: &mut Progress, key: &str, value: &str) {
    match key {
        "frame" => progress.frames = value.trim().parse().unwrap_or(0),
        "fps" => progress.fps = numeric_prefix(value),
        "bitrate" => progress.bitrate_kbps = numeric_prefix(value),
        "total_size" => progress.total_bytes = value.trim().parse().unwrap_or(0),
        "out_time_us" => progress.out_time_s = numeric_prefix(value) / 1_000_000.0,
        "drop_frames" => progress.dropped = value.trim().parse().unwrap_or(0),
        "dup_frames" => progress.duplicated = value.trim().parse().unwrap_or(0),
        "speed" => progress.speed = numeric_prefix(value),
        _ => {}
    }
}

/// Build the publish URL. RTMP keys are a path segment, not a query parameter.
fn publish_url(server: &str, key: &str) -> String {
    format!("{}/{}", server.trim_end_matches('/'), key.trim())
}

/// The arguments that make an endless loop cheap.
///
/// `-re` paces reading at real time, `-stream_loop -1` restarts the file
/// forever, and `-c copy` remuxes without re-encoding, so a loop costs almost
/// no CPU regardless of how many run at once.
fn loop_args(file: &str, url: &str) -> Vec<String> {
    vec![
        "-hide_banner".into(),
        "-loglevel".into(),
        "warning".into(),
        "-progress".into(),
        "pipe:1".into(),
        "-fflags".into(),
        "+genpts".into(),
        "-re".into(),
        "-stream_loop".into(),
        "-1".into(),
        "-i".into(),
        file.into(),
        "-c".into(),
        "copy".into(),
        "-f".into(),
        "flv".into(),
        "-flvflags".into(),
        "no_duration_filesize".into(),
        url.into(),
    ]
}

#[tauri::command]
pub fn start_loop(
    app: AppHandle,
    supervisor: tauri::State<'_, Supervisor>,
    id: String,
    file: String,
    server: String,
    key: String,
) -> Result<(), String> {
    let tools =
        match tools::resolve() {
            ToolStatus::Ready(t) => t,
            ToolStatus::Missing { .. } => return Err(
                "ffmpeg was not found. Run ./setup.sh, or install it with `brew install ffmpeg`."
                    .into(),
            ),
        };

    if !std::path::Path::new(&file).is_file() {
        return Err(format!("No file at {file}"));
    }
    if key.trim().is_empty() {
        return Err("This destination has no stream key.".into());
    }

    {
        let running = supervisor.running.lock().unwrap();
        if running.contains_key(&id) {
            return Err("That destination is already streaming.".into());
        }
    }

    let url = publish_url(&server, &key);
    let mut child = Command::new(&tools.ffmpeg)
        .args(loop_args(&file, &url))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Could not start ffmpeg: {e}"))?;

    let stdout = child.stdout.take().ok_or("ffmpeg gave no stdout")?;
    let stderr = child.stderr.take().ok_or("ffmpeg gave no stderr")?;
    let stdin = child.stdin.take();
    let deliberate = Arc::new(Mutex::new(false));

    // Progress reader: ffmpeg writes key=value lines, each block terminated by
    // a `progress=` line.
    {
        let app = app.clone();
        let id = id.clone();
        std::thread::spawn(move || {
            let mut progress = Progress {
                id: id.clone(),
                ..Default::default()
            };
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                let Some((key, value)) = line.split_once('=') else {
                    continue;
                };
                if key == "progress" {
                    let _ = app.emit("stream:progress", progress.clone());
                } else {
                    apply(&mut progress, key, value);
                }
            }
        });
    }

    // stderr is kept as a rolling tail so a failure can be explained without
    // holding an unbounded log of a stream that has been up for days.
    let tail = Arc::new(Mutex::new(Vec::<String>::new()));
    {
        let tail = tail.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let mut tail = tail.lock().unwrap();
                tail.push(line);
                if tail.len() > 40 {
                    tail.remove(0);
                }
            }
        });
    }

    // Exit watcher: owns nothing the UI needs, so it can block until ffmpeg dies.
    {
        let app = app.clone();
        let id = id.clone();
        let deliberate = deliberate.clone();
        let supervisor_handle = app.clone();
        std::thread::spawn(move || {
            // Re-acquire the child from state rather than moving it here, so
            // `stop_loop` stays able to signal it.
            loop {
                std::thread::sleep(std::time::Duration::from_millis(400));
                let state: tauri::State<'_, Supervisor> = supervisor_handle.state();
                let mut running = state.running.lock().unwrap();
                let Some(entry) = running.get_mut(&id) else {
                    break; // stop_loop already reaped it
                };
                match entry.child.try_wait() {
                    Ok(Some(status)) => {
                        let was_deliberate = *deliberate.lock().unwrap();
                        let error = if was_deliberate {
                            None
                        } else {
                            let tail = tail.lock().unwrap();
                            Some(
                                tail.iter()
                                    .rev()
                                    .find(|l| !l.trim().is_empty())
                                    .cloned()
                                    .unwrap_or_else(|| "ffmpeg exited unexpectedly".into()),
                            )
                        };
                        running.remove(&id);
                        drop(running);
                        let _ = app.emit(
                            "stream:ended",
                            Ended {
                                id: id.clone(),
                                code: status.code(),
                                deliberate: was_deliberate,
                                error,
                            },
                        );
                        break;
                    }
                    Ok(None) => continue,
                    Err(_) => break,
                }
            }
        });
    }

    supervisor.running.lock().unwrap().insert(
        id,
        Running {
            child,
            stdin,
            deliberate,
        },
    );
    Ok(())
}

#[tauri::command]
pub fn stop_loop(supervisor: tauri::State<'_, Supervisor>, id: String) -> Result<(), String> {
    let mut running = supervisor.running.lock().unwrap();
    let Some(entry) = running.get_mut(&id) else {
        return Ok(()); // already stopped; nothing to do
    };

    *entry.deliberate.lock().unwrap() = true;

    // "q" is ffmpeg's own quit command, which lets it close the RTMP session
    // cleanly. Killing outright leaves the platform waiting on a dead socket.
    if let Some(stdin) = entry.stdin.as_mut() {
        let _ = stdin.write_all(b"q\n");
        let _ = stdin.flush();
    }
    entry.stdin = None;

    for _ in 0..20 {
        match entry.child.try_wait() {
            Ok(Some(_)) => {
                running.remove(&id);
                return Ok(());
            }
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(100)),
            Err(e) => return Err(format!("Could not stop ffmpeg: {e}")),
        }
    }

    let _ = entry.child.kill();
    let _ = entry.child.wait();
    running.remove(&id);
    Ok(())
}

#[tauri::command]
pub fn running_loops(supervisor: tauri::State<'_, Supervisor>) -> Vec<String> {
    supervisor.running.lock().unwrap().keys().cloned().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numeric_prefix_handles_ffmpeg_units() {
        assert_eq!(numeric_prefix("2651.6kbits/s"), 2651.6);
        assert_eq!(numeric_prefix("1.02x"), 1.02);
        assert_eq!(numeric_prefix("  30.00 "), 30.0);
        assert_eq!(numeric_prefix("-0.5x"), -0.5);
        // "N/A" appears before the first frame is written; it must not panic.
        assert_eq!(numeric_prefix("N/A"), 0.0);
        assert_eq!(numeric_prefix(""), 0.0);
    }

    #[test]
    fn apply_fills_a_progress_block() {
        let mut p = Progress::default();
        for (k, v) in [
            ("frame", "667"),
            ("fps", "30.00"),
            ("bitrate", "2651.6kbits/s"),
            ("total_size", "7357631"),
            ("out_time_us", "22198345"),
            ("drop_frames", "2"),
            ("dup_frames", "1"),
            ("speed", "1.02x"),
            ("unknown_key", "ignored"),
        ] {
            apply(&mut p, k, v);
        }
        assert_eq!(p.frames, 667);
        assert_eq!(p.fps, 30.0);
        assert_eq!(p.bitrate_kbps, 2651.6);
        assert_eq!(p.total_bytes, 7_357_631);
        assert!((p.out_time_s - 22.198345).abs() < 1e-6);
        assert_eq!(p.dropped, 2);
        assert_eq!(p.duplicated, 1);
        assert_eq!(p.speed, 1.02);
    }

    #[test]
    fn publish_url_joins_without_doubling_slashes() {
        assert_eq!(
            publish_url("rtmp://a.rtmp.youtube.com/live2", "abcd-1234"),
            "rtmp://a.rtmp.youtube.com/live2/abcd-1234"
        );
        assert_eq!(
            publish_url("rtmp://a.rtmp.youtube.com/live2/", "abcd-1234"),
            "rtmp://a.rtmp.youtube.com/live2/abcd-1234"
        );
        // A pasted key often carries trailing whitespace.
        assert_eq!(publish_url("rtmp://x/live", "  key  "), "rtmp://x/live/key");
    }

    #[test]
    fn loop_args_keep_the_cheap_path() {
        let args = loop_args("/videos/pipo.mp4", "rtmp://x/live/key");
        let joined = args.join(" ");
        // -re and -stream_loop are input options: they must precede -i.
        let i = args.iter().position(|a| a == "-i").unwrap();
        assert!(args.iter().position(|a| a == "-re").unwrap() < i);
        assert!(args.iter().position(|a| a == "-stream_loop").unwrap() < i);
        assert_eq!(args[i + 1], "/videos/pipo.mp4");
        // -c copy is what keeps a loop nearly free; losing it would silently
        // turn every stream into a full re-encode.
        assert!(joined.contains("-c copy"));
        assert!(joined.contains("-stream_loop -1"));
        assert!(joined.contains("-f flv"));
        assert_eq!(args.last().unwrap(), "rtmp://x/live/key");
    }
}
