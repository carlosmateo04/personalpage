//! Stream keys, kept out of the configuration file.
//!
//! On macOS they live in the login Keychain, so they are encrypted at rest and
//! never appear in anything that could be copied, synced, or committed by
//! accident. Elsewhere — only development machines — they are held in memory,
//! which is deliberately useless for real use but keeps the app buildable and
//! testable off macOS.

#[cfg(target_os = "macos")]
mod backend {
    /// Deliberately still the old identifier, and it must stay that way.
    ///
    /// The Keychain finds an entry by (service, account). Renaming the app to
    /// Caudal and renaming this at the same time would point the lookup at a
    /// service that has never been written to: every saved stream key would
    /// silently read back as absent, every destination would show as not ready,
    /// and a 24/7 stream would refuse to start after an update with no error to
    /// explain why. The string is invisible to the user; losing their keys is
    /// not. If it is ever changed, it needs a migration that reads from here
    /// first and is tested on macOS, not a rename.
    const SERVICE: &str = "com.streambridge.desktop";

    fn entry(id: &str) -> Result<keyring::Entry, String> {
        keyring::Entry::new(SERVICE, id).map_err(|e| format!("Keychain unavailable: {e}"))
    }

    pub fn set(id: &str, key: &str) -> Result<(), String> {
        entry(id)?
            .set_password(key)
            .map_err(|e| format!("Could not save to the Keychain: {e}"))
    }

    pub fn get(id: &str) -> Result<Option<String>, String> {
        match entry(id)?.get_password() {
            Ok(k) => Ok(Some(k)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(format!("Could not read from the Keychain: {e}")),
        }
    }

    pub fn delete(id: &str) -> Result<(), String> {
        match entry(id)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(format!("Could not remove from the Keychain: {e}")),
        }
    }
}

#[cfg(not(target_os = "macos"))]
mod backend {
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};

    fn store() -> &'static Mutex<HashMap<String, String>> {
        static STORE: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();
        STORE.get_or_init(|| Mutex::new(HashMap::new()))
    }

    pub fn set(id: &str, key: &str) -> Result<(), String> {
        store().lock().unwrap().insert(id.into(), key.into());
        Ok(())
    }

    pub fn get(id: &str) -> Result<Option<String>, String> {
        Ok(store().lock().unwrap().get(id).cloned())
    }

    pub fn delete(id: &str) -> Result<(), String> {
        store().lock().unwrap().remove(id);
        Ok(())
    }
}

/// Direct access for other modules, without going through a command.
///
/// The commands are the frontend's door; these are the crate's. Same store,
/// same guarantees — a value written here is as protected as a stream key.
pub fn put(key: &str, value: &str) -> Result<(), String> {
    backend::set(key, value)
}

pub fn take(key: &str) -> Result<Option<String>, String> {
    backend::get(key)
}

pub fn forget(key: &str) -> Result<(), String> {
    backend::delete(key)
}

/// Where a given destination's key is stored, so the UI can say so honestly.
#[tauri::command]
pub fn secret_store() -> &'static str {
    if cfg!(target_os = "macos") {
        "keychain"
    } else {
        "memory"
    }
}

#[tauri::command]
pub fn set_secret(id: String, key: String) -> Result<(), String> {
    if key.trim().is_empty() {
        return Err("An empty key cannot be saved.".into());
    }
    backend::set(&id, key.trim())
}

#[tauri::command]
pub fn get_secret(id: String) -> Result<Option<String>, String> {
    backend::get(&id)
}

#[tauri::command]
pub fn delete_secret(id: String) -> Result<(), String> {
    backend::delete(&id)
}

/// Which of the given destinations have a key stored, so the UI can show
/// readiness without ever pulling the keys themselves into the frontend.
#[tauri::command]
pub fn which_have_secrets(ids: Vec<String>) -> Vec<String> {
    ids.into_iter()
        .filter(|id| matches!(backend::get(id), Ok(Some(_))))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_round_trips_and_can_be_removed() {
        let id = "test-destination-roundtrip";
        set_secret(id.into(), "abcd-1234-efgh".into()).unwrap();
        assert_eq!(
            get_secret(id.into()).unwrap().as_deref(),
            Some("abcd-1234-efgh")
        );
        delete_secret(id.into()).unwrap();
        assert_eq!(get_secret(id.into()).unwrap(), None);
    }

    #[test]
    fn keys_are_trimmed_and_blanks_refused() {
        let id = "test-destination-trim";
        set_secret(id.into(), "  spaced-key  ".into()).unwrap();
        assert_eq!(
            get_secret(id.into()).unwrap().as_deref(),
            Some("spaced-key")
        );
        assert!(set_secret(id.into(), "   ".into()).is_err());
        delete_secret(id.into()).unwrap();
    }

    #[test]
    fn removing_a_key_that_was_never_set_is_not_an_error() {
        assert!(delete_secret("test-destination-absent".into()).is_ok());
    }

    #[test]
    fn readiness_reports_only_the_ids_that_have_keys() {
        set_secret("test-has-key".into(), "k".into()).unwrap();
        let found = which_have_secrets(vec!["test-has-key".into(), "test-lacks-key".into()]);
        assert_eq!(found, vec!["test-has-key".to_string()]);
        delete_secret("test-has-key".into()).unwrap();
    }
}
