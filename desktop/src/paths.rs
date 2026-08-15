// Data directory resolution.
//
// Precedence, checked in order:
//   1. UNLATCHED_HOME environment variable, if set.
//   2. %APPDATA%/Unlatched on Windows.
//   3. ~/.config/unlatched everywhere else.
// The directory is created on first use so callers never have to check for
// its existence before opening files inside it.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

pub fn data_dir() -> PathBuf {
    ensure_exists(resolve_data_dir())
}

fn resolve_data_dir() -> PathBuf {
    if let Ok(home) = env::var("UNLATCHED_HOME") {
        if !home.trim().is_empty() {
            return PathBuf::from(home);
        }
    }
    resolve_platform_default_home()
}

// The profile registry (profiles.json) always lives here, never under
// whatever UNLATCHED_HOME happens to be at the moment: it is the one
// address every launch can find regardless of which profile is active, so
// switching profiles never makes the list of profiles itself unreachable,
// and a launch scoped to an isolated UNLATCHED_HOME (a test harness, a
// scripted run) never reads or writes the real registry.
pub fn platform_default_home() -> PathBuf {
    ensure_exists(resolve_platform_default_home())
}

fn ensure_exists(dir: PathBuf) -> PathBuf {
    if !dir.exists() {
        // Ignore the error here: every subsequent file operation will
        // surface a clear error of its own if creation actually failed.
        let _ = fs::create_dir_all(&dir);
    }
    dir
}

fn resolve_platform_default_home() -> PathBuf {
    if cfg!(target_os = "windows") {
        if let Ok(appdata) = env::var("APPDATA") {
            return PathBuf::from(appdata).join("Unlatched");
        }
    }

    if let Ok(home) = env::var("HOME") {
        return PathBuf::from(home).join(".config").join("unlatched");
    }
    // Windows without APPDATA, or a POSIX-like shell without HOME: fall
    // back to USERPROFILE, which is set in both native and MSYS shells.
    if let Ok(profile) = env::var("USERPROFILE") {
        return PathBuf::from(profile).join(".config").join("unlatched");
    }

    // Last resort so the app still runs somewhere rather than panicking.
    PathBuf::from(".").join("unlatched-data")
}

/// The person's Downloads folder, or None if it cannot be found.
///
/// None is a real answer rather than a guess: somebody who has relocated
/// Downloads (or is on a machine where it was never created) would otherwise
/// get a file written into a folder they do not use, and be told it went to
/// "Downloads". The caller falls back to asking where to put it, which is
/// slower but never lies about the destination.
pub fn downloads_dir() -> Option<PathBuf> {
    let base = env::var("USERPROFILE")
        .or_else(|_| env::var("HOME"))
        .ok()?;
    let dir = PathBuf::from(base).join("Downloads");
    dir.is_dir().then_some(dir)
}

/// A path inside `dir` for `name` that does not already exist, by adding
/// " (2)", " (3)" and so on before the extension - the convention every
/// browser uses, so it needs no explanation.
///
/// Never overwrites. Downloading a resume is a recovery action, and the file
/// most likely to share the name is the copy the person is trying to recover.
pub fn non_clobbering_path(dir: &Path, name: &str) -> PathBuf {
    let candidate = dir.join(name);
    if !candidate.exists() {
        return candidate;
    }
    let (stem, ext) = match name.rsplit_once('.') {
        Some((stem, ext)) => (stem, format!(".{ext}")),
        None => (name, String::new()),
    };
    for n in 2..1000 {
        let candidate = dir.join(format!("{stem} ({n}){ext}"));
        if !candidate.exists() {
            return candidate;
        }
    }
    dir.join(name)
}

pub fn db_path(home: &Path) -> PathBuf {
    home.join("unlatched.db")
}

pub fn config_path(home: &Path) -> PathBuf {
    home.join("config.json")
}

// Desktop-only settings (child process invocation, window state, and other
// things that are not part of the shared config.json contract) live in
// their own file so the two front ends never fight over the same document.
pub fn desktop_settings_path(home: &Path) -> PathBuf {
    home.join("desktop_settings.json")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_the_extension_when_avoiding_a_clash() {
        let dir = env::temp_dir().join("unlatched-clobber-test");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();

        let first = non_clobbering_path(&dir, "original-20260805T101112.docx");
        assert_eq!(first.file_name().unwrap(), "original-20260805T101112.docx");
        fs::write(&first, b"x").unwrap();

        let second = non_clobbering_path(&dir, "original-20260805T101112.docx");
        assert_eq!(
            second.file_name().unwrap(),
            "original-20260805T101112 (2).docx"
        );
        // The suffix goes BEFORE the extension: "resume.docx (2)" would open
        // in nothing, and Word would refuse the file it just saved.
        fs::write(&second, b"x").unwrap();
        let third = non_clobbering_path(&dir, "original-20260805T101112.docx");
        assert_eq!(
            third.file_name().unwrap(),
            "original-20260805T101112 (3).docx"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn handles_a_name_with_no_extension() {
        let dir = env::temp_dir().join("unlatched-clobber-noext");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();

        fs::write(dir.join("resume"), b"x").unwrap();
        assert_eq!(
            non_clobbering_path(&dir, "resume").file_name().unwrap(),
            "resume (2)"
        );

        let _ = fs::remove_dir_all(&dir);
    }
}
