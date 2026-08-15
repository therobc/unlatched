"""unmask.py - Match postings by distinctive phrase, with a cap on how common
a phrase is allowed to be before it stops counting as evidence.

WHY A DOCUMENT-FREQUENCY CAP
--------------------------------------------
An earlier version resolved anonymised postings ("About Our Client: the
organization operates in the fintech industry...") back to a real employer
by phrase-matching against a corpus of known postings, on the theory that a
recruiter almost always pastes the client's own text. That worked, until a
boilerplate phrase - EEO language present in dozens of unrelated postings -
matched everything it touched and silently merged eight unrelated roles into
one company.

The fix is a document-frequency cap: a phrase that appears in more than
`MAX_DF` documents across the corpus carries no identifying weight,
whatever else is true about it. The same idea applies anywhere "distinctive
text" is being used as an identifier - a shingle is only evidence if it is
RARE, and rarity has to be measured, not assumed from a stopword list.

SAFETY: everything here reads stored text. Nothing is prompted or executed.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

# A shingle appearing in more than this many documents (including itself) is
# boilerplate, not an identifier.
MAX_DF = 3
MIN_RARE_SHARED = 2

# Ordinary English function words carry no identifying weight in a shingle -
# a run made mostly of these is exactly the shape boilerplate takes.
COMMON = {
    "the", "and", "for", "with", "that", "this", "from", "will", "you", "our",
    "are", "have", "has", "was", "were", "their", "they", "not", "but",
    "all", "any", "can", "may", "who", "your", "his", "her", "its", "job",
    "role", "work", "team", "support", "customer", "technical", "service",
    "services", "experience", "skills", "ability", "years", "position",
    "company", "organization", "client", "candidate", "required", "preferred",
}


def shingles(text: str, n: int = 8, cap: int = 45) -> list[str]:
    """Distinctive n-word runs, preferring those made mostly of uncommon
    words. Boilerplate phrases appear in enough postings to match everything,
    so a run only qualifies here when most of its words are outside COMMON -
    that is a pre-filter, not the safety property; the document-frequency
    cap below is what actually protects the match.
    """
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'.-]*", text or "")
    out = []
    for i in range(0, max(0, len(words) - n), 3):
        run = words[i:i + n]
        rare = sum(1 for w in run if w.lower() not in COMMON and len(w) > 3)
        if rare >= n - 3:
            out.append((rare, " ".join(run)))
    out.sort(key=lambda x: -x[0])
    return [s for _, s in out[:cap]]


def document_frequency(phrase: str, corpus: dict[str, str]) -> int:
    needle = phrase.lower()
    return sum(1 for text in corpus.values() if needle in (text or "").lower())


def distinctive_phrases(text: str, corpus: dict[str, str],
                         max_df: int = MAX_DF) -> list[str]:
    """Shingles of `text` whose document frequency across `corpus` (this
    document included) is low enough to still identify something. A phrase
    with df <= 1 is unique to this one document, which is not evidence
    either - there is nothing else to match it against.
    """
    out = []
    for sh in shingles(text):
        freq = document_frequency(sh, corpus)
        if 1 < freq <= max_df:
            out.append(sh)
    return out


def match_by_phrase(key: str, corpus: dict[str, str],
                     max_df: int = MAX_DF,
                     min_shared: int = MIN_RARE_SHARED) -> list[tuple[str, int, list[str]]]:
    """Find other documents in `corpus` that share rare phrases with
    `corpus[key]`. Returns [(other_key, shared_count, phrases)], best first.
    """
    text = corpus.get(key, "")
    if len(text or "") < 400:
        return []
    hits: Counter[str] = Counter()
    why: dict[str, list[str]] = defaultdict(list)
    for sh in distinctive_phrases(text, corpus, max_df=max_df):
        needle = sh.lower()
        for other_key, other_text in corpus.items():
            if other_key == key:
                continue
            if needle in (other_text or "").lower():
                hits[other_key] += 1
                why[other_key].append(sh)
    return [(k, n, why[k]) for k, n in hits.most_common() if n >= min_shared]
