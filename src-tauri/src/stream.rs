use crate::diagnose::{diagnose, Issue};
use crate::tools::{self, ToolStatus};
use serde::Serialize;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager};

/// A run shorter than this is treated as a failed attempt; anything longer
/// counts as having worked, and resets the backoff. Without this, a stream that
/// reconnects successfully every few hours would creep up to the maximum delay
/// and stay there.
const STABLE_AFTER: Duration = Duration::from_secs(60);

/// Backoff ceiling. Capped rather than unbounded because an unattended stream
/// should keep trying indefinitely — an outage that lasts six hours must still
/// recover on its own.
const MAX_BACKOFF_SECS: u64 = 30;

fn backoff_secs(attempt: u32) -> u64 {
    2u64.saturating_pow(attempt.min(8)).min(MAX_BACKOFF_SECS)
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

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
    /// How many times this destination has reconnected during the session.
    pub reconnects: u32,
}

/// Lifecycle transitions the backend drives, mirrored by the UI.
#[derive(Serialize, Clone)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Connecting,
    Live,
    Reconnecting,
    Failed,
    Stopped,
}

#[derive(Serialize, Clone)]
pub struct StatusEvent {
    pub id: String,
    pub phase: Phase,
    /// Reconnection attempt number, zero on a first connection.
    pub attempt: u32,
    /// Seconds until the next attempt, when reconnecting.
    pub retry_in: u64,
    pub issue: Option<Issue>,
    /// Total reconnects since the destination was started.
    pub reconnects: u32,
}

/// A durable record of something worth knowing about after the fact — the point
/// of an unattended stream is that nobody was watching when it happened.
#[derive(Serialize, Clone)]
pub struct Incident {
    pub id: String,
    pub at: u64,
    pub kind: String,
    pub message: String,
}

struct SessionHandle {
    stop: Arc<AtomicBool>,
    /// The ffmpeg currently running, so a stop can signal it straight away.
    child: Arc<Mutex<Option<Child>>>,
    /// Cuts a backoff sleep short when the user stops mid-wait.
    waker: Arc<(Mutex<bool>, Condvar)>,
}

#[derive(Default)]
pub struct Supervisor {
    sessions: Mutex<HashMap<String, SessionHandle>>,
    /// `caffeinate`, held for as long as anything is streaming.
    keep_awake: Mutex<Option<Child>>,
    incidents: Mutex<Vec<Incident>>,
}

impl Supervisor {
    fn log(&self, app: &AppHandle, id: &str, kind: &str, message: String) {
        let incident = Incident {
            id: id.to_string(),
            at: now_ms(),
            kind: kind.to_string(),
            message,
        };
        {
            let mut log = self.incidents.lock().unwrap();
            log.push(incident.clone());
            // A month of hourly hiccups still fits; anything older is noise.
            if log.len() > 500 {
                log.remove(0);
            }
        }
        let _ = app.emit("stream:incident", incident);
    }

    /// Keep the Mac awake while anything is streaming.
    ///
    /// `caffeinate` is a system binary, which avoids linking IOKit for a single
    /// assertion. `-i` blocks idle sleep, `-m` keeps disks spun up, `-s` blocks
    /// system sleep while on mains power. Closing the lid on battery still
    /// sleeps — macOS does not allow otherwise.
    fn acquire_wakelock(&self) {
        #[cfg(target_os = "macos")]
        {
            let mut lock = self.keep_awake.lock().unwrap();
            if lock.is_some() {
                return;
            }
            *lock = Command::new("/usr/bin/caffeinate")
                .args(["-i", "-m", "-s"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .ok();
        }
    }

    fn release_wakelock(&self) {
        let mut lock = self.keep_awake.lock().unwrap();
        if let Some(mut child) = lock.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    fn release_wakelock_if_idle(&self) {
        if self.sessions.lock().unwrap().is_empty() {
            self.release_wakelock();
        }
    }

    /// Stop everything and let go of the wake lock. Called on app exit so no
    /// ffmpeg is left orphaned, still uploading, with nothing able to stop it.
    pub fn shutdown(&self) {
        let ids: Vec<String> = self.sessions.lock().unwrap().keys().cloned().collect();
        for id in ids {
            self.request_stop(&id);
        }
        // Give each ffmpeg a moment to close its RTMP session cleanly.
        for _ in 0..30 {
            if self.sessions.lock().unwrap().is_empty() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        for handle in self.sessions.lock().unwrap().values() {
            if let Some(child) = handle.child.lock().unwrap().as_mut() {
                let _ = child.kill();
            }
        }
        self.release_wakelock();
    }

    fn request_stop(&self, id: &str) {
        let sessions = self.sessions.lock().unwrap();
        let Some(handle) = sessions.get(id) else {
            return;
        };
        handle.stop.store(true, Ordering::SeqCst);

        // "q" is ffmpeg's own quit command; it closes the RTMP session properly
        // rather than leaving the platform waiting on a dead socket.
        if let Some(child) = handle.child.lock().unwrap().as_mut() {
            if let Some(stdin) = child.stdin.as_mut() {
                let _ = stdin.write_all(b"q\n");
                let _ = stdin.flush();
            }
        }

        let (lock, cv) = &*handle.waker;
        *lock.lock().unwrap() = true;
        cv.notify_all();
    }
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

/// What the supervisor does after one ffmpeg exits.
#[derive(Debug, PartialEq)]
pub enum Next {
    /// The user asked for this; leave quietly.
    Stop,
    /// Nothing more will help; show the reason and stay down.
    Fail(Box<Issue>),
    /// Try again after `delay` seconds.
    Retry {
        attempt: u32,
        delay: u64,
        issue: Box<Issue>,
    },
}

/// The reconnection policy, kept pure so it can be exercised without a
/// streaming platform, a network, or a Tauri runtime. Everything that decides
/// whether an unattended stream survives the night lives in this function.
pub fn decide(
    stopped: bool,
    ran_for: Duration,
    attempt: u32,
    stderr: &str,
    exit_code: Option<i32>,
) -> Next {
    if stopped {
        return Next::Stop;
    }

    let issue = diagnose(stderr, exit_code);
    if !issue.retryable {
        return Next::Fail(Box::new(issue));
    }

    // A run that lasted is evidence the setup works, so the next failure starts
    // its backoff from scratch. Without this, a stream that drops once an hour
    // would creep to the maximum delay and stay there for good.
    let next_attempt = if ran_for >= STABLE_AFTER {
        1
    } else {
        attempt + 1
    };

    Next::Retry {
        attempt: next_attempt,
        delay: backoff_secs(next_attempt),
        issue: Box::new(issue),
    }
}

/// Sleep, unless a stop arrives first. Returns true when stopped.
fn wait_or_stop(waker: &Arc<(Mutex<bool>, Condvar)>, secs: u64) -> bool {
    let (lock, cv) = &**waker;
    let stopped = lock.lock().unwrap();
    if *stopped {
        return true;
    }
    let (guard, _) = cv.wait_timeout(stopped, Duration::from_secs(secs)).unwrap();
    *guard
}

#[tauri::command]
pub fn start_loop(
    app: AppHandle,
    supervisor: tauri::State<'_, Supervisor>,
    id: String,
    file: String,
    server: String,
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

    // The key is read here rather than passed in, so it never crosses the IPC
    // boundary and never exists in the frontend at all.
    let key = crate::secrets::get_secret(id.clone())?
        .ok_or("This destination has no stream key saved.")?;
    if key.trim().is_empty() {
        return Err("This destination has no stream key.".into());
    }
    if supervisor.sessions.lock().unwrap().contains_key(&id) {
        return Err("That destination is already streaming.".into());
    }

    let stop = Arc::new(AtomicBool::new(false));
    let child_slot: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
    let waker = Arc::new((Mutex::new(false), Condvar::new()));

    supervisor.sessions.lock().unwrap().insert(
        id.clone(),
        SessionHandle {
            stop: stop.clone(),
            child: child_slot.clone(),
            waker: waker.clone(),
        },
    );
    supervisor.acquire_wakelock();

    let url = publish_url(&server, &key);
    let ffmpeg = tools.ffmpeg.clone();

    // The session thread owns the retry loop. It outlives any single ffmpeg,
    // which is what turns a dropped connection into a reconnection rather than
    // the end of the stream.
    std::thread::spawn(move || {
        let state: tauri::State<'_, Supervisor> = app.state();
        let mut attempt: u32 = 0;
        let mut reconnects: u32 = 0;

        loop {
            if stop.load(Ordering::SeqCst) {
                break;
            }

            let _ = app.emit(
                "stream:status",
                StatusEvent {
                    id: id.clone(),
                    phase: Phase::Connecting,
                    attempt,
                    retry_in: 0,
                    issue: None,
                    reconnects,
                },
            );

            let spawned = Command::new(&ffmpeg)
                .args(loop_args(&file, &url))
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn();

            let mut child = match spawned {
                Ok(c) => c,
                Err(e) => {
                    let issue = diagnose(&format!("could not start ffmpeg: {e}"), None);
                    state.log(&app, &id, "failed", issue.title.clone());
                    let _ = app.emit(
                        "stream:status",
                        StatusEvent {
                            id: id.clone(),
                            phase: Phase::Failed,
                            attempt,
                            retry_in: 0,
                            issue: Some(issue),
                            reconnects,
                        },
                    );
                    break;
                }
            };

            let stdout = child.stdout.take();
            let stderr = child.stderr.take();
            *child_slot.lock().unwrap() = Some(child);

            let started = Instant::now();

            let progress_thread = stdout.map(|stdout| {
                let app = app.clone();
                let id = id.clone();
                let attempt_at_start = attempt;
                std::thread::spawn(move || {
                    let mut progress = Progress {
                        id: id.clone(),
                        reconnects,
                        ..Default::default()
                    };
                    // Frames actually leaving is the only proof the platform
                    // accepted the connection; the process merely running is not.
                    let mut announced_live = false;

                    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                        let Some((key, value)) = line.split_once('=') else {
                            continue;
                        };
                        if key == "progress" {
                            if !announced_live && progress.frames > 0 {
                                announced_live = true;
                                let _ = app.emit(
                                    "stream:status",
                                    StatusEvent {
                                        id: id.clone(),
                                        phase: Phase::Live,
                                        attempt: attempt_at_start,
                                        retry_in: 0,
                                        issue: None,
                                        reconnects,
                                    },
                                );
                            }
                            let _ = app.emit("stream:progress", progress.clone());
                        } else {
                            apply(&mut progress, key, value);
                        }
                    }
                })
            });

            // A rolling tail, so a stream that has been up for days can still
            // explain its final moments without an unbounded log.
            let tail = Arc::new(Mutex::new(Vec::<String>::new()));
            let stderr_thread = stderr.map(|stderr| {
                let tail = tail.clone();
                std::thread::spawn(move || {
                    for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                        let mut tail = tail.lock().unwrap();
                        tail.push(line);
                        if tail.len() > 40 {
                            tail.remove(0);
                        }
                    }
                })
            });

            // Poll rather than block, so `stop` can take the child's lock to
            // signal it at any moment.
            let exit_code = loop {
                std::thread::sleep(Duration::from_millis(250));
                let mut slot = child_slot.lock().unwrap();
                let Some(c) = slot.as_mut() else { break None };
                match c.try_wait() {
                    Ok(Some(status)) => break status.code(),
                    Ok(None) => continue,
                    Err(_) => break None,
                }
            };

            if let Some(t) = progress_thread {
                let _ = t.join();
            }
            if let Some(t) = stderr_thread {
                let _ = t.join();
            }
            if let Some(mut c) = child_slot.lock().unwrap().take() {
                let _ = c.wait();
            }

            if stop.load(Ordering::SeqCst) {
                state.log(&app, &id, "stopped", "Stopped from the app".into());
                let _ = app.emit(
                    "stream:status",
                    StatusEvent {
                        id: id.clone(),
                        phase: Phase::Stopped,
                        attempt,
                        retry_in: 0,
                        issue: None,
                        reconnects,
                    },
                );
                break;
            }

            let stderr_text = tail.lock().unwrap().join("\n");

            let (delay, issue) =
                match decide(false, started.elapsed(), attempt, &stderr_text, exit_code) {
                    Next::Stop => break,
                    Next::Fail(issue) => {
                        state.log(&app, &id, "failed", issue.title.clone());
                        let _ = app.emit(
                            "stream:status",
                            StatusEvent {
                                id: id.clone(),
                                phase: Phase::Failed,
                                attempt,
                                retry_in: 0,
                                issue: Some(*issue),
                                reconnects,
                            },
                        );
                        break;
                    }
                    Next::Retry {
                        attempt: next,
                        delay,
                        issue,
                    } => {
                        attempt = next;
                        (delay, *issue)
                    }
                };

            reconnects += 1;
            state.log(
                &app,
                &id,
                "reconnect",
                format!("{} — retrying in {delay}s (attempt {attempt})", issue.title),
            );
            let _ = app.emit(
                "stream:status",
                StatusEvent {
                    id: id.clone(),
                    phase: Phase::Reconnecting,
                    attempt,
                    retry_in: delay,
                    issue: Some(issue),
                    reconnects,
                },
            );

            if wait_or_stop(&waker, delay) {
                let _ = app.emit(
                    "stream:status",
                    StatusEvent {
                        id: id.clone(),
                        phase: Phase::Stopped,
                        attempt,
                        retry_in: 0,
                        issue: None,
                        reconnects,
                    },
                );
                break;
            }
        }

        state.sessions.lock().unwrap().remove(&id);
        state.release_wakelock_if_idle();
    });

    Ok(())
}

#[tauri::command]
pub fn stop_loop(supervisor: tauri::State<'_, Supervisor>, id: String) -> Result<(), String> {
    supervisor.request_stop(&id);
    Ok(())
}

#[tauri::command]
pub fn running_loops(supervisor: tauri::State<'_, Supervisor>) -> Vec<String> {
    supervisor
        .sessions
        .lock()
        .unwrap()
        .keys()
        .cloned()
        .collect()
}

#[tauri::command]
pub fn incidents(supervisor: tauri::State<'_, Supervisor>) -> Vec<Incident> {
    supervisor.incidents.lock().unwrap().clone()
}

/// Whether the Mac is currently being held awake, so the UI can say so.
#[tauri::command]
pub fn keeping_awake(supervisor: tauri::State<'_, Supervisor>) -> bool {
    supervisor.keep_awake.lock().unwrap().is_some()
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
        assert_eq!(p.bitrate_kbps, 2651.6);
        assert_eq!(p.total_bytes, 7_357_631);
        assert!((p.out_time_s - 22.198345).abs() < 1e-6);
        assert_eq!(p.dropped, 2);
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
        assert_eq!(publish_url("rtmp://x/live", "  key  "), "rtmp://x/live/key");
    }

    #[test]
    fn loop_args_keep_the_cheap_path() {
        let args = loop_args("/videos/pipo.mp4", "rtmp://x/live/key");
        let joined = args.join(" ");
        let i = args.iter().position(|a| a == "-i").unwrap();
        assert!(args.iter().position(|a| a == "-re").unwrap() < i);
        assert!(args.iter().position(|a| a == "-stream_loop").unwrap() < i);
        assert_eq!(args[i + 1], "/videos/pipo.mp4");
        assert!(joined.contains("-c copy"));
        assert!(joined.contains("-stream_loop -1"));
        assert_eq!(args.last().unwrap(), "rtmp://x/live/key");
    }

    #[test]
    fn backoff_grows_then_settles() {
        assert_eq!(backoff_secs(1), 2);
        assert_eq!(backoff_secs(2), 4);
        assert_eq!(backoff_secs(3), 8);
        assert_eq!(backoff_secs(4), 16);
        assert_eq!(backoff_secs(5), MAX_BACKOFF_SECS);
        // An outage lasting hours must not push the delay to infinity: the
        // stream has to still be trying when the connection comes back.
        assert_eq!(backoff_secs(500), MAX_BACKOFF_SECS);
    }

    #[test]
    fn a_stop_cuts_the_backoff_short() {
        let waker = Arc::new((Mutex::new(false), Condvar::new()));
        let w = waker.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(60));
            let (lock, cv) = &*w;
            *lock.lock().unwrap() = true;
            cv.notify_all();
        });
        let began = Instant::now();
        assert!(
            wait_or_stop(&waker, 30),
            "should report having been stopped"
        );
        assert!(
            began.elapsed() < Duration::from_secs(2),
            "waited {:?}; a stop must not sit out the whole backoff",
            began.elapsed()
        );
    }

    #[test]
    fn waiting_without_a_stop_runs_its_course() {
        let waker = Arc::new((Mutex::new(false), Condvar::new()));
        let began = Instant::now();
        assert!(!wait_or_stop(&waker, 1));
        assert!(began.elapsed() >= Duration::from_millis(900));
    }
}

#[cfg(test)]
mod policy_tests {
    use super::*;
    use crate::diagnose::Action;

    const SHORT: Duration = Duration::from_secs(3);
    const LONG: Duration = Duration::from_secs(120);

    const DROPPED: &str = "av_interleaved_write_frame(): Broken pipe";
    const BAD_KEY: &str = "[flv @ 0x1] Server error: NetStream.Publish.BadName";

    #[test]
    fn a_deliberate_stop_ends_it() {
        assert_eq!(decide(true, LONG, 0, DROPPED, Some(1)), Next::Stop);
        // Even a fatal-looking error must not override an explicit stop.
        assert_eq!(decide(true, SHORT, 5, BAD_KEY, Some(1)), Next::Stop);
    }

    #[test]
    fn a_rejected_key_stops_rather_than_hammering_the_platform() {
        match decide(false, SHORT, 0, BAD_KEY, Some(1)) {
            Next::Fail(issue) => assert_eq!(issue.action, Action::Reauth),
            other => panic!("expected a failure, got {other:?}"),
        }
    }

    #[test]
    fn a_dropped_connection_retries_with_growing_delays() {
        let delays: Vec<u64> = (0..6)
            .scan(0u32, |attempt, _| {
                match decide(false, SHORT, *attempt, DROPPED, Some(1)) {
                    Next::Retry {
                        attempt: next,
                        delay,
                        ..
                    } => {
                        *attempt = next;
                        Some(delay)
                    }
                    other => panic!("expected a retry, got {other:?}"),
                }
            })
            .collect();
        assert_eq!(delays, vec![2, 4, 8, 16, 30, 30]);
    }

    #[test]
    fn a_long_run_resets_the_backoff() {
        // A stream that held for two minutes and then dropped is a fresh
        // incident, not the sixth failure of a hopeless loop. Without this, a
        // stream that hiccups once an hour would settle at the maximum delay
        // and stay there for the rest of its life.
        match decide(false, LONG, 7, DROPPED, Some(1)) {
            Next::Retry { attempt, delay, .. } => {
                assert_eq!(attempt, 1);
                assert_eq!(delay, 2);
            }
            other => panic!("expected a retry, got {other:?}"),
        }
    }

    #[test]
    fn a_short_run_does_not_reset_the_backoff() {
        match decide(false, SHORT, 7, DROPPED, Some(1)) {
            Next::Retry { attempt, delay, .. } => {
                assert_eq!(attempt, 8);
                assert_eq!(delay, MAX_BACKOFF_SECS);
            }
            other => panic!("expected a retry, got {other:?}"),
        }
    }

    #[test]
    fn an_outage_lasting_hours_is_still_being_retried() {
        // 12 hours of failing every 30 seconds. The stream must still be
        // trying when the connection finally returns.
        let mut attempt = 0u32;
        for _ in 0..1440 {
            match decide(false, SHORT, attempt, "Network is unreachable", Some(1)) {
                Next::Retry {
                    attempt: next,
                    delay,
                    ..
                } => {
                    assert!(delay <= MAX_BACKOFF_SECS);
                    attempt = next;
                }
                other => panic!("gave up after {attempt} attempts: {other:?}"),
            }
        }
    }

    #[test]
    fn the_reason_travels_with_the_retry() {
        match decide(false, SHORT, 0, "Connection refused", Some(1)) {
            Next::Retry { issue, .. } => {
                assert!(issue.retryable);
                assert!(issue.raw.contains("Connection refused"));
            }
            other => panic!("expected a retry, got {other:?}"),
        }
    }
}
