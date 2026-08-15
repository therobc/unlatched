// Keywords: the deterministic half of resume optimization, mirroring the
// CLI's `unlatched keywords`. Aggregates the same inflection-aware matcher
// idea as unlatched/coverage.py's present() over every stored job
// description instead of one posting, so it reads as "what does the market
// keep asking for, and which of it does the resume never evidence".
//
// SIMPLIFICATIONS vs the Python matcher (documented per SPEC.md, not
// accidental drift): word boundaries here come from splitting text into
// runs of ASCII alphanumeric characters rather than a regex \b lookaround,
// so any separator (space, hyphen, slash, comma, punctuation) between
// words in a multi-word skill is treated the same, where Python's matcher
// only allows whitespace/hyphen/slash between the words of a term. A
// resume that is a .docx file is not parsed at all - this app has no docx
// reader - so a configured .docx resume_path is treated as empty resume
// text, same as an unset one: every skill reports unevidenced rather than
// guessing at partial content.

use std::collections::{HashMap, HashSet};

use eframe::egui;

use crate::app::UnlatchedApp;

#[derive(Debug, Clone)]
pub struct KeywordDemand {
    pub skill: String,
    pub demand: usize,
    pub pct: f64,
    pub evidenced: bool,
}

/// Lowercases once per document, so the tokens below can BORROW from it.
///
/// The previous version built a `Vec<String>` - one heap allocation per token.
/// Over a full corpus that is millions of allocations before any matching
/// starts, and it froze the app the moment "All jobs" widened the corpus from
/// 82 postings to 6,347.
fn lowered(text: &str) -> String {
    text.to_lowercase()
}

/// Runs of ASCII alphanumerics, as slices into an already-lowercased string.
fn tokens_of(lowered: &str) -> Vec<&str> {
    let bytes = lowered.as_bytes();
    let mut out = Vec::new();
    let mut start = None;
    for (i, b) in bytes.iter().enumerate() {
        if b.is_ascii_alphanumeric() {
            start.get_or_insert(i);
        } else if let Some(from) = start.take() {
            out.push(&lowered[from..i]);
        }
    }
    if let Some(from) = start {
        out.push(&lowered[from..]);
    }
    out
}

/// Does `token` match `base` in one of the ordinary inflections
/// coverage.present() allows: a plain plural (-s/-es), or the -ing/-ed/-es
/// form of a verb whose trailing "e" is dropped before the suffix
/// ("diagnose" -> "diagnosing"). Mirrors coverage.py's regex alternation
/// term by term rather than approximating it.
fn matches_inflected(token: &str, base: &str) -> bool {
    // Suffix arithmetic, NOT format!. The previous version built five Strings
    // per comparison, and this is the innermost call in the whole report - it
    // runs once per token per skill, so those allocations dominated everything
    // else and hung the UI thread on a full corpus.
    if token == base {
        return true;
    }
    if let Some(rest) = token.strip_prefix(base) {
        if rest == "s" || rest == "es" {
            return true;
        }
    }
    let stem = base.strip_suffix('e').unwrap_or(base);
    match token.strip_prefix(stem) {
        Some(rest) => rest == "ing" || rest == "ed" || rest == "es",
        None => false,
    }
}

/// True if `term_tokens` appears in `haystack_tokens` as a contiguous run,
/// with only the final word of a multi-word term allowed to inflect -
/// coverage.present() only ever appends a suffix to the whole term, never
/// to an interior word.
fn present(term_tokens: &[&str], haystack_tokens: &[&str]) -> bool {
    let n = term_tokens.len();
    if n == 0 || haystack_tokens.len() < n {
        return false;
    }
    haystack_tokens.windows(n).any(|window| {
        window[..n - 1] == term_tokens[..n - 1]
            && matches_inflected(window[n - 1], term_tokens[n - 1])
    })
}

/// Resume text for the demand-report matcher. Plain text/markdown files
/// are read as-is; see the module-level note on why .docx is skipped.
pub fn load_resume_text(resume_path: Option<&str>) -> String {
    let Some(path) = resume_path else {
        return String::new();
    };
    if path.trim().is_empty() || path.to_lowercase().ends_with(".docx") {
        return String::new();
    }
    std::fs::read_to_string(path).unwrap_or_default()
}

/// Pure computation over already-loaded inputs, mirroring
/// unlatched.keywords.demand_report(): for each configured skill, how many
/// corpus documents mention it and whether the resume evidences it. Ties
/// in demand keep `skills`' own order, since Vec::sort_by is stable.
pub fn compute_report(
    corpus: &[String],
    skills: &[String],
    resume_text: &str,
) -> Vec<KeywordDemand> {
    if skills.is_empty() || corpus.is_empty() {
        return Vec::new();
    }
    // One lowercase allocation per document, then borrowed tokens.
    let lowered_corpus: Vec<String> = corpus.iter().map(|d| lowered(d)).collect();
    let corpus_tokens: Vec<Vec<&str>> = lowered_corpus.iter().map(|d| tokens_of(d)).collect();

    // INVERTED INDEX: token -> the documents containing it.
    //
    // Scanning every document for every skill is skills x documents x tokens,
    // which on a real profile is ~200 million window comparisons and froze the
    // UI thread. The index turns the common case into a handful of hash
    // lookups, and it is EXACT rather than an approximation:
    //   * a single-word term matches a document iff that document contains one
    //     of its six inflected forms, so the answer is the union of six
    //     postings lists;
    //   * a multi-word term only ever inflects its LAST word, so any match
    //     must contain the term's FIRST word verbatim - the windowed scan then
    //     runs on that short candidate list instead of the whole corpus.
    let mut index: HashMap<&str, Vec<u32>> = HashMap::new();
    for (doc, tokens) in corpus_tokens.iter().enumerate() {
        let doc = doc as u32;
        for token in tokens {
            let postings = index.entry(*token).or_default();
            // Tokens repeat constantly inside one document; postings lists are
            // built in document order, so the last entry is enough to dedup.
            if postings.last() != Some(&doc) {
                postings.push(doc);
            }
        }
    }

    let lowered_resume = lowered(resume_text);
    let resume_tokens = tokens_of(&lowered_resume);
    let resume_has_text = !resume_text.trim().is_empty();
    let corpus_size = corpus.len() as f64;

    let mut report: Vec<KeywordDemand> = Vec::with_capacity(skills.len());
    for skill in skills.iter().filter(|s| !s.trim().is_empty()) {
        let lowered_skill = lowered(skill);
        let term_tokens = tokens_of(&lowered_skill);
        let demand = match term_tokens.len() {
            0 => 0,
            1 => {
                let base = term_tokens[0];
                let stem = base.strip_suffix('e').unwrap_or(base);
                let forms = [
                    base.to_string(),
                    format!("{base}s"),
                    format!("{base}es"),
                    format!("{stem}ing"),
                    format!("{stem}ed"),
                    format!("{stem}es"),
                ];
                let mut hits: HashSet<u32> = HashSet::new();
                for form in &forms {
                    if let Some(postings) = index.get(form.as_str()) {
                        hits.extend(postings.iter().copied());
                    }
                }
                hits.len()
            }
            _ => match index.get(term_tokens[0]) {
                Some(candidates) => candidates
                    .iter()
                    .filter(|doc| present(&term_tokens, &corpus_tokens[**doc as usize]))
                    .count(),
                None => 0,
            },
        };
        let pct = ((demand as f64 / corpus_size) * 1000.0).round() / 10.0;
        report.push(KeywordDemand {
            skill: skill.clone(),
            demand,
            pct,
            evidenced: resume_has_text && present(&term_tokens, &resume_tokens),
        });
    }

    report.sort_by(|a, b| b.demand.cmp(&a.demand));
    report
}

pub fn show(app: &mut UnlatchedApp, ui: &mut egui::Ui) {
    ui.horizontal(|ui| {
        ui.heading("Keywords");
        ui.add_space(16.0);
        if ui
            .selectable_label(!app.keywords_show_all, "Qualified only")
            .clicked()
            && app.keywords_show_all
        {
            app.keywords_show_all = false;
            app.refresh_keywords();
        }
        if ui
            .selectable_label(app.keywords_show_all, "All jobs")
            .clicked()
            && !app.keywords_show_all
        {
            app.keywords_show_all = true;
            app.refresh_keywords();
        }
        if ui.button("Refresh").clicked() {
            app.refresh_keywords();
        }
    });
    ui.separator();

    if let Some(msg) = &app.keywords_message {
        ui.colored_label(egui::Color32::LIGHT_RED, msg);
        return;
    }

    if app.config.skills.is_empty() {
        ui.label("No skills configured - set config.skills on the Config tab to enable this.");
        return;
    }
    if app.keywords_corpus_size == 0 {
        let scope = if app.keywords_show_all {
            "jobs"
        } else {
            "qualified jobs"
        };
        ui.label(format!(
            "No {scope} in the database yet - nothing to measure demand against."
        ));
        return;
    }

    ui.label(format!(
        "{} skills tracked over {} postings",
        app.config.skills.len(),
        app.keywords_corpus_size
    ));
    ui.add_space(6.0);

    let gaps: Vec<&KeywordDemand> = app
        .keywords_report
        .iter()
        .filter(|r| r.demand > 0 && !r.evidenced)
        .collect();
    let covered: Vec<&KeywordDemand> = app.keywords_report.iter().filter(|r| r.evidenced).collect();

    egui::ScrollArea::vertical()
        .auto_shrink([false, false])
        .show(ui, |ui| {
            ui.strong(format!("GAPS ({} demanded, not evidenced)", gaps.len()));
            if gaps.is_empty() {
                ui.label("None - every demanded skill is evidenced in the resume.");
            } else {
                for r in &gaps {
                    render_row(ui, r);
                }
            }
            ui.add_space(10.0);
            ui.strong(format!(
                "COVERED ({} demanded and evidenced)",
                covered.len()
            ));
            if covered.is_empty() {
                ui.label("None yet.");
            } else {
                for r in &covered {
                    render_row(ui, r);
                }
            }
        });
}

fn render_row(ui: &mut egui::Ui, r: &KeywordDemand) {
    ui.horizontal(|ui| {
        ui.monospace(format!("{:<28}", r.skill));
        ui.monospace(format!("demand {:>3}", r.demand));
        ui.monospace(format!("{:>5.1}%", r.pct));
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    #[test]
    fn inflections_match_the_python_matcher() {
        assert!(matches_inflected("troubleshooting", "troubleshooting"));
        assert!(matches_inflected("diagnoses", "diagnose"));
        assert!(matches_inflected("diagnosing", "diagnose"));
        assert!(matches_inflected("diagnosed", "diagnose"));
        assert!(matches_inflected("licenses", "license"));
        // A shorter term must never match inside an unrelated longer word.
        assert!(!matches_inflected("software", "soft"));
        assert!(!matches_inflected("email", "ai"));
    }

    #[test]
    fn tokens_borrow_and_split_on_punctuation() {
        let low = lowered("Help-Desk, Tier II / on-call!");
        assert_eq!(tokens_of(&low), ["help", "desk", "tier", "ii", "on", "call"]);
    }

    #[test]
    fn multi_word_terms_match_only_as_a_contiguous_run() {
        let low = lowered("We need customer service experience.");
        let toks = tokens_of(&low);
        let term = lowered("customer service");
        assert!(present(&tokens_of(&term), &toks));
        let apart = lowered("customer satisfaction and service");
        assert!(!present(&tokens_of(&term), &tokens_of(&apart)));
    }

    /// The index must return exactly what the old exhaustive scan returned.
    /// A faster wrong answer is worse than a slow right one, and the failure
    /// mode here is silent: a skill quietly reporting the wrong demand looks
    /// like a real market signal.
    #[test]
    fn the_index_agrees_with_an_exhaustive_scan() {
        let corpus: Vec<String> = vec![
            "Customer service and troubleshooting for field teams".to_string(),
            "We diagnose network faults; customer satisfaction matters".to_string(),
            "Diagnosing hardware, Active Directory, help desk tier II".to_string(),
            "Nothing relevant here at all".to_string(),
            "CUSTOMER SERVICE, again - shouting does not change the match".to_string(),
            "service customer, reversed order must NOT match the phrase".to_string(),
            "licenses, licensed, licensing all inflect from license".to_string(),
        ];
        let skills = [
            "Customer Service",
            "Diagnose",
            "Troubleshooting",
            "Active Directory",
            "License",
            "Help Desk",
            "Kubernetes",
        ];

        let lowered_corpus: Vec<String> = corpus.iter().map(|d| lowered(d)).collect();
        let corpus_tokens: Vec<Vec<&str>> =
            lowered_corpus.iter().map(|d| tokens_of(d)).collect();

        let owned: Vec<String> = skills.iter().map(|s| (*s).to_string()).collect();
        let report = compute_report(&corpus, &owned, "");

        for (i, skill) in skills.iter().enumerate() {
            let low = lowered(skill);
            let term = tokens_of(&low);
            let brute = corpus_tokens
                .iter()
                .filter(|toks| present(&term, toks))
                .count();
            assert_eq!(
                report[i].demand, brute,
                "{skill}: index said {}, exhaustive scan said {brute}",
                report[i].demand
            );
        }
    }

    /// The report at the scale that froze the app: "All jobs" on a real
    /// profile is ~6,300 postings against ~97 configured skills. The old
    /// matcher built five Strings per token comparison and the old tokenizer
    /// one per token, so this took long enough that Windows marked the window
    /// Not Responding.
    #[test]
    fn a_full_corpus_report_is_fast_enough_for_the_ui_thread() {
        let doc = "We are hiring a technical support specialist to troubleshoot \
                   hardware, diagnose network faults, run Active Directory and own \
                   customer service for a field team of twelve people. "
            .repeat(6);
        let corpus: Vec<String> = (0..6_300).map(|_| doc.clone()).collect();
        let skills: Vec<String> = (0..97)
            .map(|i| format!("skill number {i}"))
            .chain(["Customer Service".to_string(), "Diagnose".to_string()])
            .collect();

        let started = Instant::now();
        let report = compute_report(&corpus, &skills, "diagnosed networks");
        let elapsed = started.elapsed();

        assert_eq!(report.len(), skills.len());
        assert!(
            elapsed.as_secs_f32() < 3.0,
            "full-corpus report took {elapsed:?}; it used to hang the UI thread"
        );
    }
}
