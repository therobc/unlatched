"""A phrase appearing in more than MAX_DF documents is boilerplate, not an
identifier, and must not be used to match postings together - the original
defect merged eight unrelated roles into one company because an EEO
boilerplate phrase matched everything it touched.
"""
from __future__ import annotations

from unlatched import unmask

BOILERPLATE = (
    "qualified applicants will receive consideration for employment "
    "without regard to race color religion sex national origin"
)

# Buffer made entirely of common/short words (unmask.COMMON, or words too
# short to count) - shingles.rare_count treats it as zero rare words, so it
# cannot combine with either neighbour to form a spuriously rare shingle at
# the boundary between distinctive text and boilerplate.
BUFFER = "and the this that with for all any but not you our are have has was"

DISTINCTIVE_A = (
    "we build the aurora flight telemetry platform used by regional "
    "cargo carriers across the pacific northwest corridor"
)
DISTINCTIVE_B = (
    "our proprietary basalt inventory reconciliation engine powers "
    "warehouse operations for independent hardware retailers nationwide"
)


def _doc(distinctive: str) -> str:
    return f"{distinctive}. {BUFFER}. {BOILERPLATE}. {BUFFER}. " + ("filler word " * 40)


def test_boilerplate_phrase_exceeds_df_cap_and_is_discarded():
    # The boilerplate phrase appears in every document; a distinctive one
    # appears in only two.
    corpus = {
        "a": _doc(DISTINCTIVE_A),
        "b": _doc(DISTINCTIVE_A),
        "c": _doc("no distinctive content in this one at all"),
        "d": _doc("nor in this one either, nothing distinctive here"),
        "e": _doc("and nothing distinctive appears in this document"),
    }
    freq = unmask.document_frequency(BOILERPLATE, corpus)
    assert freq > unmask.MAX_DF

    phrases = unmask.distinctive_phrases(corpus["a"], corpus, max_df=unmask.MAX_DF)
    for phrase in phrases:
        assert unmask.document_frequency(phrase, corpus) <= unmask.MAX_DF
    joined = " ".join(phrases).lower()
    assert "qualified applicants" not in joined
    assert "consideration for employment" not in joined
    # The distinctive content itself must still come through.
    assert "aurora" in joined or "pacific northwest" in joined


def test_rare_shared_phrase_still_matches_within_the_cap():
    corpus = {
        "a": _doc(DISTINCTIVE_A),
        "b": _doc(DISTINCTIVE_A),
        "c": _doc(DISTINCTIVE_B),
        # Padding so the shared BOILERPLATE sentence (present in every
        # document here) exceeds MAX_DF and cannot itself count as a match -
        # otherwise a 3-document corpus puts it exactly AT the cap.
        "d": _doc("nothing distinctive in this document either"),
        "e": _doc("still nothing distinctive appears here"),
    }
    matches = unmask.match_by_phrase("a", corpus, max_df=unmask.MAX_DF)
    matched_keys = [k for k, _n, _p in matches]
    assert "b" in matched_keys
    assert "c" not in matched_keys


def test_eight_way_false_merge_does_not_happen():
    """The original failure mode: eight unrelated postings all resolving to
    one company because of a shared boilerplate phrase. With the cap
    applied, an anonymised posting sharing ONLY boilerplate with seven
    others must not report any of them as a match."""
    corpus = {f"unrelated-{i}": _doc(f"distinct filler set number {i} here today")
              for i in range(7)}
    corpus["anon"] = _doc("no distinctive content here at all just boilerplate text")
    matches = unmask.match_by_phrase("anon", corpus, max_df=unmask.MAX_DF)
    assert matches == []
