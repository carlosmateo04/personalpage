use serde::Serialize;

/// What the user can do about a problem.
#[derive(Serialize, Clone, Copy, PartialEq, Debug)]
#[serde(rename_all = "lowercase")]
pub enum Action {
    /// Try the same thing again.
    Retry,
    /// The stream key needs replacing.
    Reauth,
    /// The video file needs attention.
    File,
    /// Something about the machine or network.
    Network,
}

/// A failure explained in terms a user can act on.
///
/// `retryable` is the load-bearing field: it decides whether the supervisor
/// keeps trying. Reconnecting forever against a rejected key would hammer the
/// platform and never recover, so that case has to be told apart from a network
/// blip, which recovers on its own given a few seconds.
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Issue {
    pub title: String,
    pub detail: String,
    pub action: Action,
    pub retryable: bool,
    /// The ffmpeg line this was derived from, kept for the details disclosure.
    pub raw: String,
}

struct Rule {
    /// Any of these appearing in ffmpeg's output identifies this failure.
    needles: &'static [&'static str],
    title: &'static str,
    detail: &'static str,
    action: Action,
    retryable: bool,
}

/// Ordered most specific first: a rejected key also mentions the connection, so
/// the authentication rules have to win over the generic network ones.
const RULES: &[Rule] = &[
    Rule {
        needles: &[
            "NetStream.Publish.BadName",
            "Authentication Failed",
            "Server returned 403",
            "returned error: 403",
            "Publish.Denied",
        ],
        title: "Stream key rejected",
        detail: "The platform refused this key. It may have expired, been regenerated, or belong to a different channel. Copy it again from the platform and replace it here.",
        action: Action::Reauth,
        retryable: false,
    },
    Rule {
        needles: &["NetStream.Publish.Start not received", "Publish.BadConnection"],
        title: "Platform did not accept the broadcast",
        detail: "The connection opened but the platform never confirmed the broadcast. On YouTube this usually means the key is right but the channel is not enabled for live streaming yet.",
        action: Action::Reauth,
        retryable: false,
    },
    Rule {
        needles: &["No such file or directory", "does not exist"],
        title: "Video file is missing",
        detail: "The file that was being streamed is no longer where it was. If it moved, choose it again; if it is on an external drive, check the drive is mounted.",
        action: Action::File,
        retryable: false,
    },
    Rule {
        needles: &[
            "Invalid data found when processing input",
            "moov atom not found",
            "Invalid NAL unit size",
        ],
        title: "Video file cannot be read",
        detail: "ffmpeg could not decode this file. It may be corrupt or still being written. Try a different file, or re-export this one.",
        action: Action::File,
        retryable: false,
    },
    Rule {
        needles: &[
            "Could not find tag for codec",
            "codec not currently supported in container",
        ],
        title: "Codec not supported for streaming",
        detail: "RTMP carries H.264 video and AAC audio. This file uses something else, so it has to be converted before it can be streamed.",
        action: Action::File,
        retryable: false,
    },
    Rule {
        needles: &["Permission denied"],
        title: "Permission denied",
        detail: "macOS blocked access to the file. If it lives in Desktop, Documents, or an external drive, grant Caudal access in System Settings → Privacy & Security → Files and Folders.",
        action: Action::File,
        retryable: false,
    },
    Rule {
        needles: &["Connection refused"],
        title: "Server refused the connection",
        detail: "Nothing accepted the connection at that address. Usually the platform is between broadcasts or briefly down. Retrying.",
        action: Action::Retry,
        retryable: true,
    },
    Rule {
        needles: &[
            "Temporary failure in name resolution",
            "Name or service not known",
            "Failed to resolve hostname",
            "nodename nor servname provided",
        ],
        title: "Cannot reach the internet",
        detail: "The server address could not be looked up, which normally means the connection is down. Retrying until it comes back.",
        action: Action::Network,
        retryable: true,
    },
    Rule {
        needles: &["Network is unreachable", "No route to host", "Host is down"],
        title: "Network unreachable",
        detail: "The Mac has no working route to the platform. Retrying until the connection returns.",
        action: Action::Network,
        retryable: true,
    },
    Rule {
        needles: &[
            "Connection timed out",
            "Operation timed out",
            "timed out",
            "Timeout",
        ],
        title: "Connection timed out",
        detail: "The platform stopped responding. Usually a network hiccup. Retrying.",
        action: Action::Network,
        retryable: true,
    },
    Rule {
        needles: &[
            "Broken pipe",
            "Connection reset by peer",
            "End of file",
            "Error writing trailer",
            "Server error",
        ],
        title: "Connection dropped",
        detail: "The platform closed the connection mid-stream. Common on long broadcasts. Reconnecting.",
        action: Action::Retry,
        retryable: true,
    },
    Rule {
        needles: &["Error opening output", "Input/output error"],
        title: "Could not reach the platform",
        detail: "The connection to the streaming server could not be opened. Usually the internet is down or the address is unreachable. Retrying until it returns.",
        action: Action::Network,
        retryable: true,
    },
    Rule {
        needles: &["TLS", "SSL", "Error in the pull function", "handshake"],
        title: "Secure connection failed",
        detail: "The encrypted connection to the platform could not be established or was interrupted. Retrying.",
        action: Action::Network,
        retryable: true,
    },
];

/// Turn ffmpeg's output into something actionable.
///
/// Unrecognised failures are treated as retryable on purpose: for an
/// unattended stream, retrying something transient is far cheaper than giving
/// up on something that would have recovered. The backoff keeps a genuinely
/// hopeless case from hammering the platform.
pub fn diagnose(stderr: &str, exit_code: Option<i32>) -> Issue {
    let haystack = stderr.to_lowercase();

    for rule in RULES {
        if rule
            .needles
            .iter()
            .any(|n| haystack.contains(&n.to_lowercase()))
        {
            return Issue {
                title: rule.title.into(),
                detail: rule.detail.into(),
                action: rule.action,
                retryable: rule.retryable,
                raw: stderr.trim().to_string(),
            };
        }
    }

    Issue {
        title: "Stream stopped unexpectedly".into(),
        detail: format!(
            "ffmpeg exited{} without a recognised reason. Reconnecting in case it was transient.",
            exit_code
                .map(|c| format!(" with code {c}"))
                .unwrap_or_default()
        ),
        action: Action::Retry,
        retryable: true,
        raw: if stderr.trim().is_empty() {
            "no output from ffmpeg".into()
        } else {
            stderr.trim().to_string()
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Real lines ffmpeg emits, so the matching is checked against what it
    /// actually prints rather than what it might.
    #[test]
    fn a_rejected_key_is_not_retried() {
        for line in [
            "[flv @ 0x7f8] Server error: NetStream.Publish.BadName",
            "rtmps://live-api-s.facebook.com: Server returned 403 Forbidden",
            "[rtmp @ 0x14f] Server error: Authentication Failed.",
        ] {
            let issue = diagnose(line, Some(1));
            assert!(!issue.retryable, "must not retry a bad key: {line}");
            assert_eq!(issue.action, Action::Reauth);
        }
    }

    #[test]
    fn network_failures_are_retried() {
        for line in [
            "tcp://a.rtmp.youtube.com:1935: Connection refused",
            "[tcp @ 0x1] Connection to tcp://a.rtmp.youtube.com:1935 failed: Network is unreachable",
            "a.rtmp.youtube.com: Temporary failure in name resolution",
            "av_interleaved_write_frame(): Broken pipe",
            "Connection to tcp://x:1935 failed: Operation timed out",
        ] {
            let issue = diagnose(line, Some(1));
            assert!(issue.retryable, "must retry a network failure: {line}");
        }
    }

    #[test]
    fn a_missing_file_is_not_retried() {
        let issue = diagnose("/Users/carlos/pipo.mp4: No such file or directory", Some(1));
        assert!(!issue.retryable);
        assert_eq!(issue.action, Action::File);
    }

    #[test]
    fn an_unstreamable_codec_is_not_retried() {
        let issue = diagnose(
            "[flv @ 0x7f9] Could not find tag for codec hevc in stream #0, codec not currently supported in container",
            Some(1),
        );
        assert!(!issue.retryable);
        assert_eq!(issue.action, Action::File);
    }

    #[test]
    fn authentication_wins_over_the_connection_wording() {
        // This line mentions both a connection and a rejection; retrying it
        // forever would be the damaging outcome, so the rejection must win.
        let issue = diagnose(
            "Connection to tcp://a.rtmp.youtube.com:1935 established\n[flv] Server error: NetStream.Publish.BadName",
            Some(1),
        );
        assert!(!issue.retryable);
        assert_eq!(issue.action, Action::Reauth);
    }

    #[test]
    fn an_unknown_failure_still_retries() {
        let issue = diagnose("something nobody has seen before", Some(255));
        assert!(issue.retryable, "unattended streams favour retrying");
        assert!(issue.detail.contains("255"));
    }

    /// Verbatim from ffmpeg 6.1.1, captured by killing an RTMP listener
    /// mid-stream, pointing at a dead port, and at a hostname that does not
    /// resolve. Guessing at these strings is how a classifier ends up matching
    /// nothing in the field.
    #[test]
    fn real_ffmpeg_output_is_classified_correctly() {
        let dropped = diagnose(
            "[aost#0:1/copy @ 0x55d2a045d840] Error submitting a packet to the muxer: Broken pipe\n\
             [out#0/flv @ 0x55d2a03c3840] Error writing trailer: Broken pipe",
            Some(1),
        );
        assert!(dropped.retryable);
        assert_eq!(dropped.title, "Connection dropped");

        let refused = diagnose(
            "[out#0/flv @ 0x559223382540] Error opening output rtmp://127.0.0.1:19999/live/k: Connection refused\n\
             Error opening output files: Connection refused",
            Some(145),
        );
        assert!(refused.retryable);
        assert_eq!(refused.title, "Server refused the connection");

        // ffmpeg does not mention DNS at all here — it reports an I/O error.
        let unresolvable = diagnose(
            "[out#0/flv @ 0x556acd1df540] Error opening output rtmp://nope.invalid/live/k: Input/output error\n\
             Error opening output files: Input/output error",
            Some(251),
        );
        assert!(unresolvable.retryable);
        assert_eq!(unresolvable.title, "Could not reach the platform");

        let missing = diagnose(
            "Error opening input file /tmp/no-such-video-file.mp4.\n\
             Error opening input files: No such file or directory",
            Some(254),
        );
        assert!(!missing.retryable, "a missing file will not fix itself");
        assert_eq!(missing.action, Action::File);
    }

    #[test]
    fn silence_produces_a_usable_message() {
        let issue = diagnose("", None);
        assert!(!issue.raw.is_empty());
        assert!(issue.retryable);
    }
}
