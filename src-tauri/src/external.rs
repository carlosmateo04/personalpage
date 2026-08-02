//! Handing a URL to the browser.
//!
//! `open` on macOS launches applications and local files as readily as web
//! pages, so what reaches it is restricted to an allowlist. Every URL this app
//! opens either comes from a network response or is assembled from a
//! configured value, and neither is trustworthy input by default.

use std::process::Command;

/// Hosts this app has a reason to open, and no others.
///
/// A list rather than a pattern. "Anything under google.com" would admit
/// `google.com.evil.test` to anyone who forgets that a suffix check needs a
/// leading dot, and the cost of naming five hosts is that the mistake cannot
/// be made.
const ALLOWED: &[&str] = &[
    "github.com",
    "objects.githubusercontent.com",
    // The consent screen, and where a client id is created.
    "accounts.google.com",
    "console.cloud.google.com",
    "developers.google.com",
    "myaccount.google.com",
    // Where a published video ends up.
    "www.youtube.com",
    "studio.youtube.com",
];

pub fn is_allowed_url(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else {
        return false;
    };
    // Stop at the first delimiter, or a path could carry an allowed name.
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    // Credentials in the authority (user@host) would move the real host past
    // this check entirely.
    if authority.contains('@') {
        return false;
    }
    let host = authority.split(':').next().unwrap_or("");
    ALLOWED.contains(&host) || host.ends_with(".githubusercontent.com")
}

/// Open a URL in the user's browser.
#[tauri::command]
pub fn open_external(url: String) -> Result<(), String> {
    if !is_allowed_url(&url) {
        return Err(format!("Refusing to open {url}"));
    }
    Command::new("/usr/bin/open")
        .arg(&url)
        .status()
        .map_err(|e| format!("Could not open the browser: {e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_hosts_this_app_actually_uses_are_allowed() {
        assert!(is_allowed_url("https://github.com/o/r/releases/download/v1/a.dmg"));
        assert!(is_allowed_url("https://objects.githubusercontent.com/x/y"));
        assert!(is_allowed_url("https://console.cloud.google.com/apis/credentials"));
        assert!(is_allowed_url("https://accounts.google.com/o/oauth2/v2/auth?client_id=x"));
        assert!(is_allowed_url("https://studio.youtube.com/channel/abc"));
    }

    #[test]
    fn everything_else_is_refused() {
        // `open` runs applications and opens local files, so anything not on
        // the list is turned away rather than filtered or corrected.
        assert!(!is_allowed_url("file:///Applications/Calculator.app"));
        assert!(!is_allowed_url("/Applications/Calculator.app"));
        assert!(!is_allowed_url("http://github.com/o/r"), "plain http is not allowed");
        assert!(!is_allowed_url("https://google.com/search"), "not a host we open");
    }

    #[test]
    fn the_near_misses_are_refused_too() {
        // A suffix that merely ends the right way.
        assert!(!is_allowed_url("https://github.com.evil.test/o/r"));
        assert!(!is_allowed_url("https://notgithub.com/o/r"));
        // An allowed name living in the path.
        assert!(!is_allowed_url("https://evil.test/github.com/a.dmg"));
        // Credentials in the authority push the real host past a naive check.
        assert!(!is_allowed_url("https://github.com@evil.test/a.dmg"));
        // A subdomain of an allowed host is still a different host.
        assert!(!is_allowed_url("https://pages.github.com/x"));
    }

    #[test]
    fn a_port_does_not_smuggle_a_host_past_the_check() {
        assert!(is_allowed_url("https://github.com:443/o/r"));
        assert!(!is_allowed_url("https://evil.test:443/github.com"));
    }
}
