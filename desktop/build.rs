//! Embed the Windows icon and version metadata into the executable.
//!
//! An earlier change, unverified history: pin Unlatched to the taskbar,
//! close it, and the pinned button loses its icon.
//!
//! The cause was not the shell icon cache and not a missing
//! AppUserModelID, which were the two leading suspects. Believed, not
//! measured here, that the executable simply had NO icon resource:
//! `ExtractIconExW` was reported to show 0 icons in Unlatched.exe against
//! 1 for notepad.exe and 1 for our own unlatched.ico. Verified
//! 2026-09-10: eframe sets the window icon at RUNTIME from
//! assets/icon.png (see main.rs's window_icon()), so the icon existed
//! only while the process was alive to supply it - correct while
//! running, nothing to draw once closed. That is exactly the reported
//! symptom.
//!
//! The icon file is the same one the installer already ships - verified
//! 2026-09-10: installer.iss's SetupIconFile, UninstallDisplayIcon and
//! every IconFilename point at the same packaging/unlatched.ico this
//! file embeds - so the exe, the Start Menu shortcut and the uninstall
//! entry cannot drift apart.

fn main() {
    // Rebuild if the icon changes, not just if the code does.
    println!("cargo:rerun-if-changed=../packaging/unlatched.ico");
    println!("cargo:rerun-if-changed=build.rs");

    #[cfg(windows)]
    {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("../packaging/unlatched.ico");
        // Shown in Task Manager, the file's Properties pane and any UAC or
        // security prompt naming the program. Blank fields there are the same
        // class of "looks like a script, not an app" tell as the missing icon.
        res.set("ProductName", "Unlatched");
        res.set("FileDescription", "Unlatched - local-first job discovery");
        res.set("LegalCopyright", "MIT licensed");
        if let Err(err) = res.compile() {
            // Do not fail a Linux or macOS build over a Windows-only resource -
            // by construction: this whole block sits behind #[cfg(windows)], so
            // it does not even run there. Never let it fail silently on Windows
            // either: the warning below is why.
            println!("cargo:warning=could not embed Windows resources: {err}");
        }
    }
}
