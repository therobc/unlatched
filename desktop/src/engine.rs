// Resolves how to invoke the Unlatched engine (the Python command-line
// tool). Consumer installs ship a frozen `engine/unlatched-engine.exe`
// next to the desktop executable; that build is preferred whenever it is
// present, since it needs no Python install at all. Developers running
// from a source checkout do not have that folder, so the app falls back
// to invoking a configured Python interpreter with `-m unlatched`.

use std::env;
use std::path::PathBuf;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EngineMode {
    /// Full path to the bundled, frozen engine executable.
    Bundled(PathBuf),
    /// Dev fallback: invoke a Python interpreter with `-m unlatched`.
    Python,
}

impl EngineMode {
    /// Short label for display in the UI, e.g. the Companies view footer.
    pub fn label(&self) -> &'static str {
        match self {
            EngineMode::Bundled(_) => "bundled engine",
            EngineMode::Python => "python (dev)",
        }
    }
}

/// Looks for `engine/unlatched-engine.exe` next to this executable's own
/// directory (`std::env::current_exe()`'s parent). The Inno Setup
/// installer lays the install directory out that way; a plain `cargo
/// build` checkout will not have that folder, so callers fall back to
/// the configured Python invocation for dev mode.
pub fn resolve() -> EngineMode {
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidate = dir.join("engine").join("unlatched-engine.exe");
            if candidate.is_file() {
                return EngineMode::Bundled(candidate);
            }
        }
    }
    EngineMode::Python
}
