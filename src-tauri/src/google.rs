//! The one-time registration of Caudal with Google.
//!
//! Distinct from connecting a channel, which is per account and entirely
//! in-app. This is the client id that tells Google *which application* is
//! asking, created once on Google's console because nothing running on a
//! desktop can create it.
//!
//! The app cannot remove the console visit, but it can remove everything
//! around it: the value is typed into a screen that explains what it is and
//! opens the right page, rather than into a configuration file nobody would
//! find.

use serde::Serialize;

/// Where the console creates one of these.
pub const CONSOLE_CREDENTIALS: &str = "https://console.cloud.google.com/apis/credentials";
pub const CONSOLE_ENABLE_API: &str =
    "https://console.cloud.google.com/apis/library/youtube.googleapis.com";
pub const CONSOLE_CONSENT: &str = "https://console.cloud.google.com/auth/audience";

const ID_KEY: &str = "google-oauth-client-id";
const SECRET_KEY: &str = "google-oauth-client-secret";

#[derive(Serialize)]
pub struct ClientStatus {
    pub configured: bool,
    /// Enough of the id to recognise it, never the whole thing.
    pub preview: String,
}

/// Why a pasted client id is not one, or None when it is.
///
/// Worth checking rather than discovering at sign-in. The console shows several
/// long opaque strings on the same screen — a project number, an API key, a
/// client secret — and pasting the wrong one produces a Google error page in a
/// browser tab, minutes later, that never names the field at fault.
pub fn client_id_problem(id: &str) -> Option<String> {
    let id = id.trim();
    if id.is_empty() {
        return Some("Paste the client ID from the Google console.".into());
    }
    if !id.ends_with(".apps.googleusercontent.com") {
        // The most likely mistake, and it looks plausible enough to be
        // pasted with confidence.
        if id.starts_with("AIza") {
            return Some(
                "That is an API key, not a client ID. The client ID is under \
                 OAuth 2.0 Client IDs and ends in .apps.googleusercontent.com."
                    .into(),
            );
        }
        if id.starts_with("GOCSPX-") {
            return Some(
                "That is the client secret, not the client ID. They sit next to \
                 each other; the ID ends in .apps.googleusercontent.com."
                    .into(),
            );
        }
        if id.chars().all(|c| c.is_ascii_digit()) {
            return Some(
                "That is the project number. The client ID is under OAuth 2.0 \
                 Client IDs and ends in .apps.googleusercontent.com."
                    .into(),
            );
        }
        return Some("A client ID ends in .apps.googleusercontent.com.".into());
    }
    // `is_none_or` is newer than this crate's MSRV of 1.77.
    if id.split('-').next().unwrap_or("").is_empty() {
        return Some("That client ID looks incomplete.".into());
    }
    None
}

/// The first characters of an id, for showing what is stored without showing
/// all of it.
pub fn preview(id: &str) -> String {
    let head: String = id.chars().take(12).collect();
    format!("{head}…apps.googleusercontent.com")
}

#[tauri::command]
pub fn set_google_client(id: String, secret: String) -> Result<(), String> {
    if let Some(problem) = client_id_problem(&id) {
        return Err(problem);
    }
    // The secret is not confidential for an installed app — it ships inside
    // the bundle and PKCE is what actually proves the exchange — but it lives
    // in the Keychain anyway. There is no reason to write it somewhere weaker
    // than the stream keys sitting beside it.
    crate::secrets::put(ID_KEY, id.trim())?;
    crate::secrets::put(SECRET_KEY, secret.trim())
}

#[tauri::command]
pub fn google_client_status() -> ClientStatus {
    match crate::secrets::take(ID_KEY) {
        Ok(Some(id)) => ClientStatus {
            configured: true,
            preview: preview(&id),
        },
        _ => ClientStatus {
            configured: false,
            preview: String::new(),
        },
    }
}

#[tauri::command]
pub fn forget_google_client() -> Result<(), String> {
    crate::secrets::forget(ID_KEY)?;
    crate::secrets::forget(SECRET_KEY)
}

/// The console pages the setup screen links to, so the UI never hard-codes a
/// URL that the allowlist would then refuse.
#[tauri::command]
pub fn google_console_urls() -> serde_json::Value {
    serde_json::json!({
        "enableApi": CONSOLE_ENABLE_API,
        "consent": CONSOLE_CONSENT,
        "credentials": CONSOLE_CREDENTIALS,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_real_desktop_client_id_is_accepted() {
        assert_eq!(
            client_id_problem("407408718192-abc123def.apps.googleusercontent.com"),
            None
        );
        // Leading and trailing space survives a copy-paste and must not fail.
        assert_eq!(
            client_id_problem("  407408718192-abc.apps.googleusercontent.com \n"),
            None
        );
    }

    #[test]
    fn the_neighbouring_values_on_the_same_screen_are_named() {
        // Each of these is a long opaque string sitting near the client ID in
        // the console. Saying "invalid" would leave someone comparing two
        // strings that both look right.
        assert!(client_id_problem("AIzaSyD-1234567890abcdefg")
            .unwrap()
            .contains("API key"));
        assert!(client_id_problem("GOCSPX-abc123def456")
            .unwrap()
            .contains("client secret"));
        assert!(client_id_problem("407408718192")
            .unwrap()
            .contains("project number"));
    }

    #[test]
    fn anything_else_is_refused_with_the_shape_to_look_for() {
        assert!(client_id_problem("").unwrap().contains("Paste the client ID"));
        let problem = client_id_problem("my-client-id").unwrap();
        assert!(problem.contains(".apps.googleusercontent.com"));
    }

    #[test]
    fn the_preview_shows_enough_to_recognise_and_no_more() {
        let p = preview("407408718192-abc123def456ghi.apps.googleusercontent.com");
        assert!(p.starts_with("407408718192"));
        assert!(!p.contains("abc123def456ghi"), "the middle is not shown");
    }
}
