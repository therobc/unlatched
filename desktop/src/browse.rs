// Where a link opens.
//
// SHIPS AS THE DEVICE DEFAULT. A job link is a web page, and the browser a
// person already chose for web pages is the right answer until they say
// otherwise - an app that decides for them is wrong on every machine but the
// one it was written on. So the default here is "hand it to the OS", exactly
// as before, and a chosen browser is a preference stored per profile.
//
// WHY A PREFERENCE IS WORTH HAVING AT ALL: job hunting is a session with its
// own logins, and the browser holding those is often not the one that opens
// email. Being able to send postings to that browser and only that browser is
// the whole point.

use crate::fmt;
use std::path::{Path, PathBuf};
use std::sync::RwLock;

/// The browser this profile opens links in, or empty for the device default.
///
/// PUBLISHED, NOT PASSED. See `use_browser`.
static CHOSEN: RwLock<String> = RwLock::new(String::new());

/// Publish the active profile's choice. Called once a frame from the same
/// place the theme is applied, so switching profile or changing the setting
/// takes effect on the next click without anything being reloaded.
pub fn use_browser(chosen: &str) {
    if let Ok(mut slot) = CHOSEN.write() {
        slot.clear();
        slot.push_str(chosen);
    }
}

fn chosen() -> String {
    CHOSEN.read().map(|s| s.clone()).unwrap_or_default()
}

/// Browsers this machine has, by the paths they install to.
///
/// A LIST OF CANDIDATES, NOT A REGISTRY READ. The Windows registry knows the
/// real answer (Clients\StartMenuInternet), but reading it costs a dependency
/// on a published, cross-platform binary to save a person one trip through a
/// file picker. Anything this list misses is still reachable by choosing the
/// executable, which is why the picker is not a fallback but the other half of
/// the feature.
#[cfg(windows)]
fn candidates() -> Vec<(&'static str, PathBuf)> {
    let mut out = Vec::new();
    let roots: Vec<PathBuf> = ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"]
        .iter()
        .filter_map(|k| std::env::var_os(k).map(PathBuf::from))
        .collect();
    let known: &[(&str, &str)] = &[
        ("Google Chrome", "Google/Chrome/Application/chrome.exe"),
        ("Microsoft Edge", "Microsoft/Edge/Application/msedge.exe"),
        ("Mozilla Firefox", "Mozilla Firefox/firefox.exe"),
        ("LibreWolf", "LibreWolf/librewolf.exe"),
        ("Brave", "BraveSoftware/Brave-Browser/Application/brave.exe"),
        ("Chromium", "Chromium/Application/chrome.exe"),
        ("Vivaldi", "Vivaldi/Application/vivaldi.exe"),
    ];
    for (name, tail) in known {
        for root in &roots {
            let path = root.join(tail.replace('/', "\\"));
            if path.is_file() && !out.iter().any(|(n, _)| n == name) {
                out.push((*name, path));
            }
        }
    }
    out
}

#[cfg(not(windows))]
fn candidates() -> Vec<(&'static str, PathBuf)> {
    let known: &[(&str, &str)] = &[
        ("Google Chrome", "/usr/bin/google-chrome"),
        ("Chromium", "/usr/bin/chromium"),
        ("Mozilla Firefox", "/usr/bin/firefox"),
        ("LibreWolf", "/usr/bin/librewolf"),
        ("Brave", "/usr/bin/brave-browser"),
    ];
    known
        .iter()
        .filter(|(_, p)| Path::new(p).is_file())
        .map(|(n, p)| (*n, PathBuf::from(p)))
        .collect()
}

/// Browsers to offer as quick picks, newest install locations first.
pub fn installed() -> Vec<(String, String)> {
    candidates()
        .into_iter()
        .map(|(name, path)| (name.to_string(), path.to_string_lossy().into_owned()))
        .collect()
}

/// The name to show for a chosen executable: the display name if this is one
/// we know, otherwise the file name. Never the whole path - it is long enough
/// to push everything else off the row.
pub fn label(chosen: &str) -> String {
    if chosen.is_empty() {
        return "System default".to_string();
    }
    for (name, path) in candidates() {
        if path.to_string_lossy().eq_ignore_ascii_case(chosen) {
            return name.to_string();
        }
    }
    Path::new(chosen)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| chosen.to_string())
}

/// Open a URL, in the chosen browser if there is one and it still exists.
///
/// FALLS BACK RATHER THAN FAILING. A browser can be uninstalled or moved
/// between the day it was chosen and the day a link is clicked, and the person
/// clicking wants the posting, not a report about their settings.
///
/// The URL is re-checked here even though every caller checks it, because this
/// is the function that hands a string to a process: `fmt::safe_link` is what
/// guarantees it starts with http(s):// and therefore cannot be read by the
/// browser as an option rather than an address.
pub fn open(ctx: &egui::Context, url: &str) {
    let Some(safe) = fmt::safe_link(url) else {
        return;
    };
    let chosen = chosen();
    if !chosen.is_empty()
        && Path::new(&chosen).is_file()
        && std::process::Command::new(&chosen).arg(safe).spawn().is_ok()
    {
        return;
    }
    ctx.open_url(egui::OpenUrl::new_tab(safe));
}

/// A link that opens where this profile says links open.
///
/// Replaces `ui.hyperlink_to`, which ends at the OS handler and offers nothing
/// in between. Same look, same hover, one decision moved.
pub fn link(ui: &mut egui::Ui, label: impl Into<String>, url: &str) -> egui::Response {
    let response = ui.link(egui::RichText::new(label.into()).color(ui.visuals().hyperlink_color));
    if response.clicked() {
        open(ui.ctx(), url);
    }
    response
}

#[cfg(test)]
mod tests {
    use super::label;

    #[test]
    fn nothing_chosen_reads_as_the_device_default() {
        assert_eq!(label(""), "System default");
    }

    #[test]
    fn an_unknown_browser_is_named_by_its_file_not_its_path() {
        // The case this exists for: a portable build, or one installed
        // somewhere the candidate list does not look. It still has to read as
        // something in a settings row.
        assert_eq!(label("D:/portable/some-browser/qutebrowser.exe"), "qutebrowser");
    }
}
