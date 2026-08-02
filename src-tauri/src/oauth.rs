//! Connecting a YouTube account, from inside the app.
//!
//! Two things get confused with each other, and only one of them can live in
//! the app:
//!
//! **Registering Caudal with Google** happens once, ever, for the whole
//! application. It produces a client id, which is how Google knows which app is
//! asking. Nothing in a desktop app can perform this — it is a form on Google's
//! console, tied to a Google account, and its quota belongs to whoever filled it
//! in. What the app *can* do is stop it being a config file: the setup screen
//! takes the id, explains what it is for, and opens the right console page.
//!
//! **Signing in to a channel** happens once per account, and is entirely in the
//! app. Press Connect, the browser opens Google's consent screen, approving it
//! returns to a loopback address this process is listening on, and the token
//! goes to the Keychain. Repeat for every channel. No copying, no pasting, no
//! terminal.
//!
//! The flow is authorization code with PKCE, which is what Google requires of
//! installed apps: the client secret ships inside the application and can be
//! read by anyone holding a copy, so it is not what proves the request is
//! genuine. The proof is that whoever redeems the code also knows the verifier
//! that produced the challenge — a value generated per attempt and never sent
//! until the exchange.

use base64::Engine;
use serde::Serialize;
use sha2::{Digest, Sha256};

/// Uploading, and reading back the channel and playlists to fill the UI.
pub const SCOPES: &[&str] = &[
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
];

pub const AUTH_ENDPOINT: &str = "https://accounts.google.com/o/oauth2/v2/auth";
pub const TOKEN_ENDPOINT: &str = "https://oauth2.googleapis.com/token";

const URL_SAFE: base64::engine::general_purpose::GeneralPurpose =
    base64::engine::general_purpose::URL_SAFE_NO_PAD;

/// One sign-in attempt's secrets. Lives only as long as the attempt.
#[derive(Debug, Clone)]
pub struct Pkce {
    pub verifier: String,
    pub challenge: String,
    /// Echoed back by Google and compared, so a stray request to the loopback
    /// listener cannot be mistaken for the reply we are waiting for.
    pub state: String,
}

/// Base64url of `n` random bytes, from the system's source.
///
/// Not a general-purpose helper: this is the value PKCE rests on. Deriving it
/// from anything predictable — a clock, a counter — would leave the exchange
/// guessable by whoever intercepted the code.
pub fn random_token(n: usize) -> Result<String, String> {
    use std::io::Read;
    let mut buf = vec![0u8; n];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut buf))
        .map_err(|e| format!("No source of randomness available: {e}"))?;
    Ok(URL_SAFE.encode(&buf))
}

pub fn challenge_for(verifier: &str) -> String {
    URL_SAFE.encode(Sha256::digest(verifier.as_bytes()))
}

pub fn new_pkce() -> Result<Pkce, String> {
    // 32 bytes encodes to 43 characters, the minimum length the spec allows
    // for a verifier and comfortably inside its 128-character maximum.
    let verifier = random_token(32)?;
    let challenge = challenge_for(&verifier);
    Ok(Pkce {
        verifier,
        challenge,
        state: random_token(16)?,
    })
}

fn escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Where to send the browser.
///
/// `access_type=offline` with `prompt=consent` is what makes Google return a
/// refresh token. Without both, a second sign-in to the same account returns
/// only an access token good for an hour — which for an app expected to publish
/// on a schedule for months means it silently stops working after the first one
/// expires.
pub fn auth_url(client_id: &str, redirect: &str, p: &Pkce) -> String {
    let params = [
        ("client_id", client_id),
        ("redirect_uri", redirect),
        ("response_type", "code"),
        ("scope", &SCOPES.join(" ")),
        ("code_challenge", &p.challenge),
        ("code_challenge_method", "S256"),
        ("state", &p.state),
        ("access_type", "offline"),
        ("prompt", "consent"),
    ];
    let query: Vec<String> = params
        .iter()
        .map(|(k, v)| format!("{}={}", k, escape(v)))
        .collect();
    format!("{AUTH_ENDPOINT}?{}", query.join("&"))
}

/// What Google sent back to the loopback listener.
#[derive(Debug, PartialEq, Serialize)]
pub enum Callback {
    Code { code: String, state: String },
    /// The user pressed Cancel, or Google refused. Both arrive this way.
    Denied(String),
}

/// Parse the request line of the browser's redirect, e.g.
/// `GET /?code=4/abc&state=xyz HTTP/1.1`.
pub fn parse_callback(request_line: &str) -> Option<Callback> {
    let path = request_line.split_whitespace().nth(1)?;
    let query = path.split_once('?')?.1;

    let mut code = None;
    let mut state = None;
    let mut error = None;
    for pair in query.split('&') {
        let (k, v) = pair.split_once('=')?;
        let v = unescape(v);
        match k {
            "code" => code = Some(v),
            "state" => state = Some(v),
            "error" => error = Some(v),
            _ => {}
        }
    }

    if let Some(e) = error {
        return Some(Callback::Denied(e));
    }
    Some(Callback::Code {
        code: code?,
        state: state?,
    })
}

fn unescape(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                match u8::from_str_radix(&s[i + 1..i + 3], 16) {
                    Ok(b) => {
                        out.push(b);
                        i += 3;
                    }
                    Err(_) => {
                        out.push(bytes[i]);
                        i += 1;
                    }
                }
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Whether a callback belongs to the attempt that is waiting for it.
///
/// The loopback port is open to anything else running on the machine, so a
/// reply is only accepted when it carries back the state this attempt sent.
pub fn accept(callback: &Callback, expected_state: &str) -> Result<String, String> {
    match callback {
        Callback::Denied(e) if e == "access_denied" => {
            Err("Sign-in was cancelled.".into())
        }
        Callback::Denied(e) => Err(format!("Google refused the sign-in: {e}")),
        Callback::Code { state, .. } if state != expected_state => {
            Err("Ignoring a sign-in response that does not belong to this attempt.".into())
        }
        Callback::Code { code, .. } => Ok(code.clone()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_challenge_is_the_sha256_of_the_verifier() {
        // The worked example from RFC 7636, which is the one thing here that
        // must match another implementation exactly or every sign-in fails.
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        assert_eq!(
            challenge_for(verifier),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn every_attempt_gets_fresh_secrets() {
        let (a, b) = (new_pkce().unwrap(), new_pkce().unwrap());
        assert_ne!(a.verifier, b.verifier);
        assert_ne!(a.state, b.state);
        assert_eq!(a.verifier.len(), 43, "the shortest length the spec allows");
        assert_eq!(a.challenge, challenge_for(&a.verifier));
        // Base64url only, or Google rejects it before the user sees anything.
        assert!(a.verifier.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
    }

    #[test]
    fn the_authorisation_url_asks_for_a_refresh_token() {
        let p = Pkce {
            verifier: "v".into(),
            challenge: "c".into(),
            state: "s".into(),
        };
        let url = auth_url("123.apps.googleusercontent.com", "http://127.0.0.1:8731", &p);

        // Without both of these Google returns an access token good for an
        // hour and nothing else, and a scheduler that publishes next Tuesday
        // stops working on Tuesday.
        assert!(url.contains("access_type=offline"));
        assert!(url.contains("prompt=consent"));

        assert!(url.contains("code_challenge=c"));
        assert!(url.contains("code_challenge_method=S256"));
        assert!(url.contains("state=s"));
        assert!(url.starts_with(AUTH_ENDPOINT));
    }

    #[test]
    fn url_components_are_escaped() {
        let p = Pkce { verifier: "v".into(), challenge: "a+b/c=".into(), state: "s".into() };
        let url = auth_url("id", "http://127.0.0.1:8731", &p);
        // A raw + in a query value means a space; a raw / and : break the
        // redirect. All three appear in real values.
        assert!(url.contains("code_challenge=a%2Bb%2Fc%3D"));
        assert!(url.contains("redirect_uri=http%3A%2F%2F127.0.0.1%3A8731"));
        // Scopes are space-separated and both must survive.
        assert!(url.contains("youtube.upload"));
        assert!(url.contains("%20"), "the scope separator is an escaped space");
    }

    #[test]
    fn a_successful_callback_yields_the_code() {
        let cb = parse_callback("GET /?code=4%2F0AX4abc-_def&state=xyz HTTP/1.1").unwrap();
        assert_eq!(
            cb,
            Callback::Code {
                // Google's codes contain a slash, which arrives percent-encoded.
                code: "4/0AX4abc-_def".into(),
                state: "xyz".into()
            }
        );
        assert_eq!(accept(&cb, "xyz").unwrap(), "4/0AX4abc-_def");
    }

    #[test]
    fn pressing_cancel_is_reported_as_a_cancellation_not_an_error() {
        let cb = parse_callback("GET /?error=access_denied&state=xyz HTTP/1.1").unwrap();
        assert_eq!(cb, Callback::Denied("access_denied".into()));
        assert_eq!(accept(&cb, "xyz").unwrap_err(), "Sign-in was cancelled.");
    }

    #[test]
    fn a_reply_from_a_different_attempt_is_refused() {
        // The loopback port is reachable by anything else on the machine, so
        // the state is what ties a response to the request that started it.
        let cb = parse_callback("GET /?code=abc&state=somebody-else HTTP/1.1").unwrap();
        assert!(accept(&cb, "ours").unwrap_err().contains("does not belong"));
    }

    #[test]
    fn requests_that_are_not_the_callback_are_ignored() {
        // Browsers ask for this unprompted the moment the page loads.
        assert_eq!(parse_callback("GET /favicon.ico HTTP/1.1"), None);
        assert_eq!(parse_callback("GET / HTTP/1.1"), None);
        assert_eq!(parse_callback(""), None);
        // Present but empty is still not a code.
        assert_eq!(parse_callback("GET /?state=xyz HTTP/1.1"), None);
    }
}
