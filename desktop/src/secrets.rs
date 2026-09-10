//! Windows DPAPI wrapping for secrets stored in config.json.
//!
//! This is the Rust half of a contract the Python engine owns
//! (`unlatched/keystore.py`) - both front ends read and write the SAME
//! config.json, so if only one of them protected the API key, the other's
//! next save would quietly write it back in the clear.
//!
//! The storage format is a tagged string in the same field the plaintext
//! used, so the config schema is unchanged:
//!
//! ```text
//! dpapi:<base64 blob>     protected by the current user's DPAPI
//! <anything else>         legacy plaintext, upgraded on the next save
//! ```
//!
//! The honest limit, same as the Python side documents: DPAPI binds the blob
//! to the logged-in user, so another account or another machine cannot unwrap
//! it - but code already running as that user can call DPAPI exactly like we
//! do. Without a passphrase prompt on every run, nothing can prevent that.

const PREFIX: &str = "dpapi:";

#[cfg(windows)]
mod win {
    use std::ffi::c_void;

    #[repr(C)]
    pub struct DataBlob {
        pub cb_data: u32,
        pub pb_data: *mut u8,
    }

    #[link(name = "crypt32")]
    unsafe extern "system" {
        pub fn CryptProtectData(
            data_in: *const DataBlob,
            data_descr: *const u16,
            optional_entropy: *const DataBlob,
            reserved: *mut c_void,
            prompt_struct: *mut c_void,
            flags: u32,
            data_out: *mut DataBlob,
        ) -> i32;

        pub fn CryptUnprotectData(
            data_in: *const DataBlob,
            data_descr: *mut *mut u16,
            optional_entropy: *const DataBlob,
            reserved: *mut c_void,
            prompt_struct: *mut c_void,
            flags: u32,
            data_out: *mut DataBlob,
        ) -> i32;
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        pub fn LocalFree(mem: *mut c_void) -> *mut c_void;
    }

    /// One crypt32 call. `None` on any failure, which callers treat as "this
    /// machine cannot protect secrets" rather than as a hard error.
    pub fn dpapi(data: &[u8], encrypt: bool) -> Option<Vec<u8>> {
        let mut input = data.to_vec();
        let source = DataBlob {
            cb_data: input.len() as u32,
            pb_data: input.as_mut_ptr(),
        };
        let mut out = DataBlob {
            cb_data: 0,
            pb_data: std::ptr::null_mut(),
        };

        // SAFETY: `source` points at a live Vec for the duration of the call,
        // `out` is written by the API and freed through LocalFree exactly
        // once below, as its contract requires.
        let ok = unsafe {
            if encrypt {
                CryptProtectData(
                    &source,
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    0,
                    &mut out,
                )
            } else {
                CryptUnprotectData(
                    &source,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    0,
                    &mut out,
                )
            }
        };

        if ok == 0 || out.pb_data.is_null() {
            return None;
        }
        // SAFETY: the API guarantees pb_data is valid for cb_data bytes.
        let bytes = unsafe { std::slice::from_raw_parts(out.pb_data, out.cb_data as usize) }.to_vec();
        unsafe { LocalFree(out.pb_data as *mut c_void) };
        Some(bytes)
    }
}

#[cfg(not(windows))]
mod win {
    /// No stdlib-reachable user-bound store off Windows; the documented
    /// fallback is to leave the value as it was.
    pub fn dpapi(_data: &[u8], _encrypt: bool) -> Option<Vec<u8>> {
        None
    }
}

// Minimal base64, so this shares no dependency with the Python side beyond
// the wire format itself.
const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

fn b64_encode(data: &[u8]) -> String {
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(B64[(n >> 18) as usize & 63] as char);
        out.push(B64[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 {
            B64[(n >> 6) as usize & 63] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            B64[n as usize & 63] as char
        } else {
            '='
        });
    }
    out
}

fn b64_decode(text: &str) -> Option<Vec<u8>> {
    let mut acc: u32 = 0;
    let mut bits = 0;
    let mut out = Vec::with_capacity(text.len() / 4 * 3);
    for ch in text.bytes() {
        if ch == b'=' {
            break;
        }
        let v = B64.iter().position(|&c| c == ch)? as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
        }
    }
    Some(out)
}

pub fn is_protected(stored: &str) -> bool {
    stored.starts_with(PREFIX)
}

/// Whether this machine can protect secrets at rest, so the Config panel can
/// tell the user which of the two states they are actually in.
pub fn available() -> bool {
    win::dpapi(b"probe", true).is_some()
}

/// Plain secret -> what belongs in config.json. Falls back to the value
/// unchanged where DPAPI is unavailable: a secret that cannot be read back
/// would be worse than one stored plainly.
pub fn protect(value: &str) -> String {
    if value.is_empty() || is_protected(value) {
        return value.to_string();
    }
    match win::dpapi(value.as_bytes(), true) {
        Some(blob) => format!("{PREFIX}{}", b64_encode(&blob)),
        None => value.to_string(),
    }
}

/// What is in config.json -> the plain secret. An untagged value is a legacy
/// plaintext key and passes through, which is what lets an existing install
/// keep working and get upgraded on its next save. A blob that will not
/// unwrap (wrong user, moved machine) becomes empty, which reads downstream
/// as "no credential" rather than as a corrupt key sent to a live API.
pub fn unprotect(stored: &str) -> String {
    if stored.is_empty() || !is_protected(stored) {
        return stored.to_string();
    }
    let Some(raw) = b64_decode(&stored[PREFIX.len()..]) else {
        return String::new();
    };
    match win::dpapi(&raw, false) {
        Some(plain) => String::from_utf8_lossy(&plain).into_owned(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_round_trips_including_both_pad_lengths() {
        for case in [&b"a"[..], b"ab", b"abc", b"abcd", b"binary\x00\xff\xfe"] {
            assert_eq!(b64_decode(&b64_encode(case)).unwrap(), case);
        }
    }

    #[test]
    fn empty_stays_empty_and_is_never_tagged() {
        assert_eq!(protect(""), "");
        assert_eq!(unprotect(""), "");
        assert!(!is_protected(""));
    }

    #[test]
    fn legacy_plaintext_passes_through() {
        assert_eq!(unprotect("KEY123"), "KEY123");
    }

    #[test]
    fn protecting_twice_does_not_double_wrap() {
        let once = protect("KEY123");
        assert_eq!(protect(&once), once);
    }

    #[test]
    fn a_blob_that_cannot_be_unwrapped_reads_as_no_credential() {
        assert_eq!(unprotect("dpapi:!!!not-base64"), "");
        assert_eq!(unprotect("dpapi:QUJDREVG"), "");
    }

    /// THE TAG IS A CONTRACT, and this file's own header says the engine owns
    /// it. Both halves read and write the same config.json, so a prefix that
    /// drifted on one side would make the other read a protected key as legacy
    /// plaintext and hand it to a live API verbatim - or write a tag the other
    /// cannot recognise, locking somebody out of their own credential with
    /// nothing on screen to explain it.
    ///
    /// Read out of the Python source rather than copied into the assertion: a
    /// hand-copied expected value drifts exactly like the thing it checks.
    #[test]
    fn both_halves_agree_on_the_tag() {
        let py = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("unlatched")
            .join("keystore.py");
        let text = std::fs::read_to_string(&py)
            .unwrap_or_else(|e| panic!("cannot read {}: {e}", py.display()));
        let theirs = text
            .split("\nPREFIX = ")
            .nth(1)
            .expect("the engine's PREFIX moved or was renamed")
            .lines()
            .next()
            .unwrap()
            .trim()
            .trim_matches('"');
        assert_eq!(theirs, PREFIX, "the two halves tag protected secrets differently");
    }

    /// And the base64 is the ordinary alphabet with ordinary padding, which
    /// is what makes a blob written by one half readable by the other. This
    /// file carries its own encoder to avoid a dependency, so nothing else
    /// would notice it drifting from the standard one.
    #[test]
    fn the_encoding_is_standard_base64() {
        // Known vectors from RFC 4648, covering both pad lengths.
        assert_eq!(b64_encode(b"f"), "Zg==");
        assert_eq!(b64_encode(b"fo"), "Zm8=");
        assert_eq!(b64_encode(b"foo"), "Zm9v");
        assert_eq!(b64_encode(b"foobar"), "Zm9vYmFy");
        // And the two characters that separate standard base64 from the
        // URL-safe variant, which the Python side does not use.
        assert_eq!(b64_encode(&[0xfb, 0xff]), "+/8=");
        assert_eq!(b64_decode("+/8=").unwrap(), vec![0xfb, 0xff]);
    }

    #[test]
    fn round_trip_returns_the_original_secret() {
        let stored = protect("KEY123");
        if available() {
            assert!(is_protected(&stored));
            assert!(!stored.contains("KEY123"));
        }
        assert_eq!(unprotect(&stored), "KEY123");
    }
}

