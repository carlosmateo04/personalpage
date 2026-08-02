//! Noticing a new version without being able to install one.
//!
//! The updater plugin verifies every download against a public key, and the
//! check is not optional — `updater.rs` calls `verify_signature(..)?` on the
//! way out of the download with no flag to skip it. Without a key pair the app
//! would fetch an entire release and then refuse it, which is worse than not
//! offering the button at all.
//!
//! So when no key is configured, do the half that still works: ask GitHub what
//! the latest release is and say so. The install stays manual, but "there is a
//! new version, here it is" is most of the value and costs nothing to set up.
//!
//! When a key *is* configured this module is not used — the real updater is
//! strictly better, and the frontend picks between them at startup.

use serde::Serialize;
use std::process::Command;

#[derive(Serialize, Clone, Debug)]
pub struct Release {
    /// Without the leading `v`, so it compares to the built-in version.
    pub version: String,
    /// Direct link to the `.dmg`, when the release published one.
    pub dmg_url: Option<String>,
    /// The release page, always present, as the fallback destination.
    pub page_url: String,
}

/// `owner/repo` from the updater endpoint, so the two cannot drift apart.
/// Deriving it beats a second constant that nobody remembers to change.
pub fn repo_from_endpoint(url: &str) -> Option<String> {
    let rest = url.strip_prefix("https://github.com/")?;
    let mut parts = rest.split('/');
    let owner = parts.next().filter(|s| !s.is_empty())?;
    let repo = parts.next().filter(|s| !s.is_empty())?;
    Some(format!("{owner}/{repo}"))
}

/// Whether `candidate` is a later version than `current`.
///
/// Compared component by component as numbers. Comparing as text would make
/// 0.2.10 look older than 0.2.9, and the tenth patch release of a series is
/// exactly when that starts to matter.
pub fn is_newer(current: &str, candidate: &str) -> bool {
    fn parts(v: &str) -> Option<Vec<u64>> {
        let v = v.trim().trim_start_matches('v');
        // Stop at any pre-release or build suffix rather than guessing at its
        // ordering; a release named 1.2.3-beta is treated as 1.2.3.
        let core = v.split(['-', '+']).next()?;
        let nums: Vec<u64> = core.split('.').map(|p| p.parse().ok()).collect::<Option<_>>()?;
        (!nums.is_empty()).then_some(nums)
    }

    // Anything unparseable is not an update. Announcing one on the strength of
    // a string nobody could read is how an app nags about a release that does
    // not exist.
    let (Some(a), Some(b)) = (parts(current), parts(candidate)) else {
        return false;
    };
    for i in 0..a.len().max(b.len()) {
        let (x, y) = (a.get(i).copied().unwrap_or(0), b.get(i).copied().unwrap_or(0));
        if y != x {
            return y > x;
        }
    }
    false
}

/// Pull the newest release out of the GitHub API response.
pub fn parse_release(body: &str) -> Option<Release> {
    let v: serde_json::Value = serde_json::from_str(body).ok()?;
    if v.get("draft") == Some(&serde_json::Value::Bool(true)) {
        return None;
    }
    let tag = v.get("tag_name")?.as_str()?;
    let dmg_url = v
        .get("assets")?
        .as_array()?
        .iter()
        .find(|a| {
            a.get("name")
                .and_then(|n| n.as_str())
                .is_some_and(|n| n.ends_with(".dmg"))
        })
        .and_then(|a| a.get("browser_download_url"))
        .and_then(|u| u.as_str())
        .map(str::to_string);

    Some(Release {
        version: tag.trim_start_matches('v').to_string(),
        dmg_url,
        page_url: v
            .get("html_url")
            .and_then(|u| u.as_str())
            .unwrap_or_default()
            .to_string(),
    })
}

/// A URL safe to hand to `open`.
///
/// `open` will launch applications and files, not only web pages, so what
/// reaches it is restricted to https on GitHub's own hosts. The URL arrives
/// from a network response; treating it as trusted input would be careless
/// even when the response comes from somewhere reasonable.
pub fn is_safe_url(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else {
        return false;
    };
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    matches!(host, "github.com" | "objects.githubusercontent.com")
        || host.ends_with(".github.com")
        || host.ends_with(".githubusercontent.com")
}

// ------------------------------------------------------------------ commands --

/// True when the build can verify and install updates by itself.
#[tauri::command]
pub fn updater_signed(app: tauri::AppHandle) -> bool {
    !pubkey(&app).is_empty()
}

fn pubkey(app: &tauri::AppHandle) -> String {
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|u| u.get("pubkey"))
        .and_then(|k| k.as_str())
        .unwrap_or_default()
        .to_string()
}

fn endpoint(app: &tauri::AppHandle) -> Option<String> {
    app.config()
        .plugins
        .0
        .get("updater")?
        .get("endpoints")?
        .as_array()?
        .first()?
        .as_str()
        .map(str::to_string)
}

/// The newest published release, or `None` when this build is already it.
#[tauri::command]
pub fn latest_release(app: tauri::AppHandle) -> Result<Option<Release>, String> {
    let repo = endpoint(&app)
        .as_deref()
        .and_then(repo_from_endpoint)
        .ok_or("No update endpoint is configured.")?;

    // curl rather than an HTTP client crate: it is on every Mac, and this app
    // already reaches for the system's tools for ffmpeg, ffprobe, netstat and
    // caffeinate. One more is cheaper than another dependency tree.
    let out = Command::new("/usr/bin/curl")
        .args([
            "-sS",
            "-f",
            "--max-time",
            "15",
            "-H",
            "Accept: application/vnd.github+json",
            "-A",
            "Caudal",
            &format!("https://api.github.com/repos/{repo}/releases/latest"),
        ])
        .output()
        .map_err(|e| format!("Could not reach GitHub: {e}"))?;

    if !out.status.success() {
        // 404 is the ordinary answer for a repository with no releases yet.
        return Ok(None);
    }

    let Some(release) = parse_release(&String::from_utf8_lossy(&out.stdout)) else {
        return Ok(None);
    };
    Ok(is_newer(&crate::version(&app), &release.version).then_some(release))
}

/// Hand a release URL to the browser.
#[tauri::command]
pub fn open_release(url: String) -> Result<(), String> {
    if !is_safe_url(&url) {
        return Err("Refusing to open a URL that is not a GitHub download.".into());
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
    fn later_versions_are_recognised() {
        assert!(is_newer("0.2.1", "0.2.2"));
        assert!(is_newer("0.2.1", "0.3.0"));
        assert!(is_newer("0.9.9", "1.0.0"));
        assert!(is_newer("0.2.1", "v0.2.2"), "the leading v is a tag convention");
    }

    #[test]
    fn the_same_version_is_not_an_update() {
        assert!(!is_newer("0.2.1", "0.2.1"));
        assert!(!is_newer("0.2.1", "v0.2.1"));
        // Missing components count as zero, so 0.3 and 0.3.0 are the same.
        assert!(!is_newer("0.3.0", "0.3"));
        assert!(!is_newer("0.3", "0.3.0"));
    }

    #[test]
    fn older_versions_are_never_offered() {
        assert!(!is_newer("0.3.0", "0.2.9"));
        assert!(!is_newer("1.0.0", "0.9.9"));
    }

    #[test]
    fn components_compare_as_numbers_not_text() {
        // The bug this exists to prevent: as text, "10" sorts before "9".
        assert!(is_newer("0.2.9", "0.2.10"));
        assert!(!is_newer("0.2.10", "0.2.9"));
        assert!(is_newer("0.9.0", "0.10.0"));
    }

    #[test]
    fn unreadable_versions_never_claim_an_update() {
        assert!(!is_newer("0.2.1", "nightly"));
        assert!(!is_newer("0.2.1", ""));
        assert!(!is_newer("", "0.2.2"));
        assert!(!is_newer("0.2.1", "0.2.x"));
    }

    #[test]
    fn a_prerelease_suffix_is_ignored_rather_than_guessed_at() {
        assert!(is_newer("0.2.1", "0.2.2-beta"));
        assert!(!is_newer("0.2.2", "0.2.2-beta"));
    }

    #[test]
    fn the_repository_comes_from_the_update_endpoint() {
        assert_eq!(
            repo_from_endpoint(
                "https://github.com/carlosmateo04/personalpage/releases/latest/download/latest.json"
            )
            .as_deref(),
            Some("carlosmateo04/personalpage")
        );
        assert_eq!(repo_from_endpoint("https://example.com/updates.json"), None);
        assert_eq!(repo_from_endpoint("https://github.com/onlyowner"), None);
    }

    const BODY: &str = r#"{
        "tag_name": "v0.2.2",
        "html_url": "https://github.com/o/r/releases/tag/v0.2.2",
        "draft": false,
        "assets": [
            {"name": "latest.json", "browser_download_url": "https://github.com/o/r/x/latest.json"},
            {"name": "Caudal_0.2.2_universal.dmg", "browser_download_url": "https://github.com/o/r/x/Caudal.dmg"}
        ]
    }"#;

    #[test]
    fn the_dmg_is_picked_out_of_the_assets() {
        let r = parse_release(BODY).unwrap();
        assert_eq!(r.version, "0.2.2", "the tag's v is stripped");
        assert_eq!(r.dmg_url.as_deref(), Some("https://github.com/o/r/x/Caudal.dmg"));
        assert_eq!(r.page_url, "https://github.com/o/r/releases/tag/v0.2.2");
    }

    #[test]
    fn a_release_with_no_dmg_still_offers_its_page() {
        let body = r#"{"tag_name":"v0.3.0","html_url":"https://github.com/o/r/releases/tag/v0.3.0","assets":[]}"#;
        let r = parse_release(body).unwrap();
        assert_eq!(r.dmg_url, None);
        assert!(r.page_url.ends_with("v0.3.0"));
    }

    #[test]
    fn drafts_and_rubbish_are_not_releases() {
        assert!(parse_release(r#"{"tag_name":"v9.9.9","draft":true,"assets":[]}"#).is_none());
        assert!(parse_release(r#"{"message":"Not Found"}"#).is_none());
        assert!(parse_release("<html>502</html>").is_none());
    }

    #[test]
    fn only_github_download_urls_reach_open() {
        assert!(is_safe_url("https://github.com/o/r/releases/download/v1/a.dmg"));
        assert!(is_safe_url("https://objects.githubusercontent.com/x/y"));
        // `open` launches applications and local files, so everything else is
        // refused rather than filtered.
        assert!(!is_safe_url("file:///Applications/Calculator.app"));
        assert!(!is_safe_url("http://github.com/o/r"));
        assert!(!is_safe_url("https://github.com.evil.test/o/r"));
        assert!(!is_safe_url("https://evil.test/github.com/a.dmg"));
        assert!(!is_safe_url("/Applications/Calculator.app"));
    }
}
