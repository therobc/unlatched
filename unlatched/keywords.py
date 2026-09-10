"""keywords.py - the deterministic half of resume optimization.

`coverage.py` answers "how does my resume score against THIS posting". The
question here is aggregate: across every job description already on disk,
what do employers keep asking for, and which of it does the resume never
evidence? That is document-frequency counting with the same inflection-aware
matcher `coverage.present()` already uses for one posting at a time, applied
across a whole corpus instead - no model, no network, same deterministic
contract as screening.

This module is pure: it takes a corpus of description strings, a skill
vocabulary, and resume text, and returns a ranked report. All I/O (reading
the database, loading the resume file) is the caller's job, which is what
keeps this trivially testable.

MINING THE VOCABULARY
----------------------
`demand_report` scores a vocabulary the user already typed - almost always a
copy of their own resume, which means "evidenced" is true for nearly every
entry and the gap list comes back empty. That happened on the first real
run: two live candidates, two useless reports. The terms have to come from
the employers instead. `mine_report` extracts 1-3 word phrases straight out
of the postings, ranks them by how many postings ask for them (not how many
times the phrase repeats in one posting), and reports each one exactly like
a configured skill - so the gap list is genuinely "what these employers ask
for that this resume never says."
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import TypedDict

from . import coverage

_WORD_RE = re.compile(r"[a-z0-9]+")

# Ordinary English function words, plus fixed recruiting-boilerplate phrases
# that carry no skill content even though none of their individual words are
# function words ("equal opportunity" - neither word is a stopword alone).
# Named constants, edited from real mined output rather than guessed up
# front, so a reader can see exactly what mining ignores and why (see
# GENERIC_SINGLE_WORDS below for the second, position-sensitive list).
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "so",
    "because", "of", "in", "on", "at", "by", "for", "with", "about",
    "against", "between", "into", "through", "during", "before", "after",
    "above", "below", "to", "from", "up", "down", "out", "off", "over",
    "under", "again", "further", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "shall", "should", "can", "could", "may", "might", "must", "this",
    "that", "these", "those", "it", "its", "as", "not", "no", "nor",
    "only", "own", "same", "such", "too", "very", "you", "your", "yours",
    "we", "our", "ours", "they", "their", "them", "he", "she", "his",
    "her", "him", "who", "whom", "which", "what", "when", "where", "why",
    "how", "all", "any", "both", "each", "every", "few", "more", "most",
    "other", "some", "one", "also", "us", "per", "etc", "within", "upon",
    "across", "including", "include", "includes", "well", "just", "like",
    # recruiting boilerplate, matched as whole n-grams
    "equal opportunity", "equal opportunity employer",
    "equal employment opportunity", "affirmative action",
    "we offer", "you will", "join our team", "we are",
    "we are looking", "we are seeking", "about us", "about the role",
    "about the company", "why join us", "what we offer", "who we are",
    "apply now", "how to apply", "our team", "our company", "our mission",
    "the ideal candidate", "the successful candidate", "ideal candidate",
    "reasonable accommodation", "veteran status", "sexual orientation",
    "gender identity", "national origin", "protected veteran",
    "background check", "drug screen", "at will", "without regard",
    "regard to race", "race color religion", "color religion sex",
    "job description", "job type", "job summary", "full time", "part time",
    "years of experience", "years experience", "experience working",
    "minimum qualifications",
    "preferred qualifications", "required qualifications", "what you",
    "you have", "you are", "you will be", "if you", "if you are",
    # the standard EEO disclosure paragraph - close to verbatim across most
    # US postings, so it shows up as a run of fragments once split into
    # sliding n-grams rather than one clean sentence.
    "qualified applicants", "qualified applicants will",
    "applicants will receive", "will receive consideration",
    "receive consideration", "consideration for employment",
    "employment without regard", "without regard to", "all qualified",
    "all qualified applicants", "employer all qualified",
    "candidate privacy", "privacy notice", "privacy policy",
    "north america", "characteristic protected", "protected by law",
    "dental and vision", "life insurance", "health insurance",
    "geographic location", "pay range", "changing lives",
    "criminal histories", "consistent with", "fair chance", "salary type",
    "legal requirements", "without regards", "cutting edge",
    "cutting edge technology", "fast paced", "fast paced environment",
})

# Single words too generic, in ANY job posting regardless of industry, to
# read as a skill on their own ("experience", "team", "candidate") even
# though the postings mention them constantly. Checked only against 1-word
# candidates - unlike STOPWORDS these must NOT gate the edges of a longer
# phrase, or a real skill like "communication skills" would be dropped for
# starting with the generic word "communication".
GENERIC_SINGLE_WORDS: frozenset[str] = frozenset({
    "experience", "time", "support", "member", "members", "information",
    "responsibility", "responsibilities", "location", "range", "need",
    "needs", "plan", "quality", "benefit", "benefits", "manager",
    "appropriate", "position", "communication", "program", "specific",
    "complex", "identify", "relevant", "assigned", "population",
    "populations", "requirement", "requirements", "role", "roles", "team",
    "teams", "work", "works", "working", "environment", "environments",
    "opportunity", "opportunities", "candidate", "candidates",
    "individual", "individuals", "organization", "organizations", "staff",
    "ability", "abilities", "duty", "duties", "function", "functions",
    "level", "levels", "related", "additional", "other", "others",
    "general", "overall", "strong", "excellent", "effective", "high",
    "good", "great", "new", "current", "existing", "various", "multiple",
    "ongoing", "essential", "follow", "serve", "community", "communities",
    "education", "disability", "disabilities", "active", "skill",
    "skills", "record", "records", "year", "years", "case", "customer",
    "customers", "company", "companies", "together", "proud", "learn",
    "ensure", "needed", "plans", "knowledge", "required", "standards",
    "effectively", "based", "goals", "resources", "communicate", "focus",
    "changing", "life", "lives", "part", "services", "service",
    "comprehensive", "salary", "eligible", "employment", "looking",
    "efficiently", "equivalent", "match", "efficient", "industry",
    "detailed", "basic", "business", "deliver", "technology", "dental",
    "vision", "insurance", "while", "employee", "minimum", "preferred",
    "people", "factors", "programs", "please", "tools", "final", "written",
    "transforming", "development", "committed", "type", "daily", "status",
    "days", "process", "bonus", "compensation", "legal", "issues", "office",
    "using", "solutions", "receive", "limited", "ready", "commitment",
    "build", "values", "career", "personal", "improvement", "performance",
    "system", "growing", "critical", "laws", "manages", "manage",
    "specialist", "problem", "field", "regardless", "here", "frequently",
    "timely", "approach", "regular", "eeoc",
    # Found by mining real postings across three unrelated corpora
    # (healthcare, logistics, IT support): bare verbs, adjectives, and
    # abstract nouns that read as skill content in isolation ("outcomes",
    # "results", "execution") but are, on inspection, the connective
    # tissue every posting is written in regardless of what the employer
    # actually wants - the same role GENERIC_SINGLE_WORDS already plays,
    # just for terms this list had not yet seen. Listed as base forms:
    # membership is checked against ordinary inflections too (see
    # _expand_inflections below), so adding "provide" here also blocks
    # "provides"/"providing"/"provided" without a separate entry for each.
    "provide", "conduct", "create", "responsible", "available",
    "necessary", "outcome", "result", "execution", "update", "future",
    "data", "driven", "maintain", "internal", "person", "address", "goal",
    "priority", "priorities", "activity", "activities", "implement",
    "diverse", "perform", "culture", "plus", "make", "organizational",
    "talent", "understanding", "join", "streamline", "validate", "verbal",
    "offer", "growth", "lead", "action", "established", "help",
    "excellence", "things", "solve", "partner", "contribute", "world",
    "take", "facing", "real", "value", "exceptional", "collaborate",
    "familiarity", "equity", "impact", "flexible", "challenges", "meet",
    "competitive", "hours", "clear", "collaborative", "first", "best",
    "improve", "success", "thrive", "successful", "care", "hiring",
    "comfortable",
    # "plan" is already listed above and covers "plans"/"planning" through
    # _expand_inflections - except "planned", which that suffix rule
    # cannot reach: English doubles the final consonant before "-ed" on a
    # short stressed verb like "plan" ("planned", not "planed"), a spelling
    # rule the simple stem-plus-suffix expansion does not model. Listed
    # here directly rather than teaching the shared expansion function
    # English consonant-doubling for the sake of one verb.
    "planned",
})

# Regular English adverbs ("independently", "efficiently") pass every check
# above - real words, not function words, longer than three characters - and
# still are not a skill on their own, because an adverb never IS the thing
# being asked for, only how it is done. Rather than list every adverb a
# posting might use (the exact one-word-at-a-time trap this module is
# trying to get out of), this is checked as a SUFFIX rule: a length-1
# candidate ending in "ly" is dropped whenever it is long enough that the
# "ly" is plausibly the adverb suffix rather than the whole word. The
# threshold is 7, not the minimum that would catch "independently", because
# "supply", "family", "comply", and "apply" are all exactly six letters and
# are common enough as domain nouns/verbs that killing them by suffix alone
# would cost more than it saves - every genuine adverb found across the
# mined healthcare/logistics/IT corpora ("independently", "efficiently",
# "effectively", "quickly", "directly", "properly") was seven letters or
# longer.
_ADVERB_MIN_LEN = 7


def _expand_inflections(base: str) -> frozenset[str]:
    """`base` plus its ordinary inflections, using the exact suffix rule
    `coverage.present` already uses for matching a term against prose:
    plural/-s/-es, or the -ing/-ed/-es forms of a verb. This is what lets
    GENERIC_SINGLE_WORDS list a verb ONCE ("ensure") and have it gate every
    conjugation ("ensuring", "ensures", "ensured") a posting happens to use,
    instead of a reader having to notice and add each inflected form by
    hand - which is exactly how "ensure" was already in this list while
    "ensuring" kept slipping through mining output before this rule.
    """
    stem = base.removesuffix("e")
    return frozenset({base, base + "s", base + "es",
                       stem + "ing", stem + "ed", stem + "es"})


def _expand_all(bases: frozenset[str]) -> frozenset[str]:
    expanded: set[str] = set()
    for base in bases:
        expanded |= _expand_inflections(base)
    return frozenset(expanded)


_GENERIC_SINGLE_WORDS_INFLECTED: frozenset[str] = _expand_all(GENERIC_SINGLE_WORDS)

# A THIRD, narrower list, checked at the START or END of a phrase of ANY
# length (unlike GENERIC_SINGLE_WORDS, which only ever gates a length-1
# candidate so it never breaks a real compound like "communication skills"
# or "customer support"). These specific words are safe to gate everywhere
# because none of them ever heads a genuine multi-word skill phrase in the
# mined corpora - they only ever open an imperative bullet ("Conduct
# assessments...", "Provide direct patient care...") or introduce an
# infinitive fragment ("ability to work", "ability to travel"). That is
# what turns "ability to work" and "conduct assessments" into fragments
# rather than noun phrases: the noun-phrase head is missing, replaced by a
# bare verb or a generic infinitive subject. Kept deliberately small and
# evidence-based (not "every verb the language has") because a wrong entry
# here breaks a phrase of every length, not just one word - "manage" is
# left out on purpose, because "managed transportation" is a real employer
# ask, not a fragment.
_FRAGMENT_EDGE_WORDS: frozenset[str] = frozenset({
    "ensure", "provide", "conduct", "serve", "create", "responsible",
    "ability", "abilities", "capacity",
})
_FRAGMENT_EDGE_WORDS_INFLECTED: frozenset[str] = _expand_all(_FRAGMENT_EDGE_WORDS)

# A single-word term demanded by more than this share of employers in the
# corpus is treated as a restatement of the field, not a distinguishing ask
# - see mine_report for the full reasoning. Multi-word phrases are exempt:
# a phrase this specific staying at high demand is still a real signal.
_UBIQUITY_PCT_CEILING = 90.0


class KeywordDemand(TypedDict):
    skill: str
    demand: int
    pct: float
    evidenced: bool


def demand_report(corpus: list[str], skills: list[str],
                   resume_text: str = "") -> list[KeywordDemand]:
    """Rank `skills` by how many documents in `corpus` mention them.

    `demand` is the count of documents containing the skill (inflection-
    aware, via coverage.present); `pct` is that count over corpus size;
    `evidenced` is whether the resume itself contains the skill. Every
    skill in the vocabulary gets an entry, including ones with zero demand
    - the point of the report is to show what is NOT being asked for too.

    Both sides of every comparison are lowercased once here, at this
    boundary, rather than per skill per document - the same reasoning
    coverage.py documents for its own single-posting comparison.

    An empty vocabulary or an empty corpus returns an empty list rather
    than raising or dividing by zero: an empty report is a real answer.
    Ties in demand keep the vocabulary's own order, since Python's sort is
    stable, so the ranking never depends on incidental input order.
    """
    corpus_size = len(corpus)
    if not skills or corpus_size == 0:
        return []

    corpus_lower = [(text or "").lower() for text in corpus]
    resume_lower = (resume_text or "").lower()

    report: list[KeywordDemand] = []
    for skill in skills:
        if not skill:
            continue
        demand = sum(1 for text_lower in corpus_lower if coverage.present(skill, text_lower))
        pct = round(100.0 * demand / corpus_size, 1)
        evidenced = bool(resume_lower) and coverage.present(skill, resume_lower)
        report.append(KeywordDemand(skill=skill, demand=demand, pct=pct, evidenced=evidenced))

    report.sort(key=lambda r: r["demand"], reverse=True)
    return report


_BOILERPLATE_PHRASES = tuple(p.split(" ") for p in STOPWORDS if " " in p)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")

# Un-decoded HTML entities ("&nbsp;" -> "nbsp" once the "&"/";" are stripped
# as non-alphanumeric) and contraction remnants ("we're" -> "we", "re";
# "they've" -> "they", "ve"; "we'll" -> "we", "ll"). Both are dropped at
# tokenization rather than added to STOPWORDS because a STOPWORDS entry
# only gates the START/END of a phrase - a leak in the MIDDLE of a 3-gram
# would still slip through ("re looking" out of "we're looking"), and
# unlike a real stopword these tokens are never legitimate content in any
# position. Single-character remnants ("s" out of "bachelor's", "t" out of
# "don't") are already caught wherever they land by the single-character
# rule in `_is_dropped`; this list is only for the two-letter remnants that
# rule cannot reach.
_ENTITY_LEAKS = frozenset({"nbsp", "amp", "quot", "rsquo", "lsquo", "re", "ve", "ll"})


def _tokenize(text: str) -> list[str]:
    # A posting's own apply/privacy-policy link tokenizes into "https",
    # "www", and URL path fragments, which pass every drop rule (long
    # enough, not a function word) yet are never a skill - stripped before
    # word-splitting rather than added to the stopword list one domain at a
    # time.
    without_urls = _URL_RE.sub(" ", text)
    return [t for t in _WORD_RE.findall(without_urls.lower()) if t not in _ENTITY_LEAKS]


def _boilerplate_mask(tokens: list[str]) -> list[bool]:
    """True at every token position covered by a fixed boilerplate phrase.

    Masking the whole matched span, rather than only dropping an n-gram that
    happens to equal a boilerplate phrase exactly, is what keeps a fragment
    like "opportunity employer" (the tail of "equal opportunity employer")
    from surviving as if it were skill content - any n-gram window touching
    a masked position is discarded in `_candidate_phrases` below.
    """
    mask = [False] * len(tokens)
    for phrase_tokens in _BOILERPLATE_PHRASES:
        n = len(phrase_tokens)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == phrase_tokens:
                for j in range(i, i + n):
                    mask[j] = True
    return mask


def _is_dropped(tokens: list[str], phrase: str) -> bool:
    """What never survives as a mined phrase.

    The first three rules were written up front; the rest came from reading
    real mined output and finding what the first three let through:

    - A single-character token ("s", "d") is never real content on its own,
      and never legitimately anchors a phrase - it is what is left behind
      when a possessive or an abbreviation gets torn apart at its
      apostrophe or period ("bachelor's degree" tokenizes to "bachelor",
      "s", "degree"; "u.s." to "u", "s"). Checked at every position, not
      just the edges, because the fragment token can land in the middle of
      a 3-gram too.
    - A phrase starting and ending on the SAME word ("authorization ...
      prior ... authorization") is never a real skill phrase, it is what a
      phrase repeated back-to-back looks like once n-grams wrap around it -
      and left unfiltered it can spuriously out-rank the real phrase it
      wraps in the containment step below, since both only ever appear in
      that one posting.
    - A phrase starting or ending on a token that begins with a digit
      ("manages over 17b", "works with 1") is a sentence fragment truncated
      mid-number, not a skill - a real number only means something with the
      unit or word next to it, which the 3-word cap has already cut off.
    - A phrase starting or ending on a word from _FRAGMENT_EDGE_WORDS
      ("conduct assessments", "ability to work") is a grammatical fragment,
      not a noun phrase: the head noun a resume-reader would recognize as
      "the skill" is missing, replaced by a bare imperative verb or a
      generic infinitive subject. See _FRAGMENT_EDGE_WORDS for why this list
      stays short and is checked at every phrase length.
    - A phrase starting or ending on a word long enough that its "ly"
      suffix is plausibly the adverb ending rather than part of a shorter
      root ("independently", "efficiently") is checked the same way as
      _FRAGMENT_EDGE_WORDS, not just at length 1: "work independently" is
      as much a fragment as bare "independently" is, the adverb just moved
      one slot over. _ADVERB_MIN_LEN keeps the cut from also catching short
      words that merely end in "ly" as part of their root ("apply",
      "supply", "family").
    - A lone word from GENERIC_SINGLE_WORDS ("experience", "team",
      "provide") passes every rule above (it is not a function word, it is
      over three characters) yet is not a skill on its own - it is the
      scaffolding every job posting is written in, in this industry or any
      other. Checked only at length 1, against the inflected form of the
      list, so it never gates a genuine longer phrase built on the same
      root ("communication skills" keeps "communication" as its first word)
      while still catching every conjugation of a listed verb without a
      separate entry per tense.
    """
    if any(len(t) <= 1 for t in tokens):
        return True
    if phrase in STOPWORDS:
        return True
    if tokens[0] in STOPWORDS or tokens[-1] in STOPWORDS:
        return True
    if all(t in STOPWORDS for t in tokens):
        return True
    if max(len(t) for t in tokens) <= 3:
        return True
    if len(tokens) > 1 and tokens[0] == tokens[-1]:
        return True
    if tokens[0][0].isdigit() or tokens[-1][0].isdigit():
        return True
    if (tokens[0] in _FRAGMENT_EDGE_WORDS_INFLECTED
            or tokens[-1] in _FRAGMENT_EDGE_WORDS_INFLECTED):
        return True
    if _looks_like_adverb(tokens[0]) or _looks_like_adverb(tokens[-1]):
        return True
    return len(tokens) == 1 and tokens[0] in _GENERIC_SINGLE_WORDS_INFLECTED


def _looks_like_adverb(word: str) -> bool:
    return len(word) >= _ADVERB_MIN_LEN and word.endswith("ly")


def _candidate_phrases(tokens: list[str]) -> set[str]:
    """The distinct 1-3 word phrases in one document that survive the drop
    rules. A set, not a list: a phrase used ten times in one posting must
    only ever contribute one document-frequency count for that document.
    """
    phrases: set[str] = set()
    mask = _boilerplate_mask(tokens)
    total = len(tokens)
    for n in (1, 2, 3):
        for i in range(total - n + 1):
            if any(mask[i:i + n]):
                continue
            gram = tokens[i:i + n]
            phrase = " ".join(gram)
            if not _is_dropped(gram, phrase):
                phrases.add(phrase)
    return phrases


def _suppress_contained(phrase_docs: dict[str, frozenset[int]]) -> dict[str, frozenset[int]]:
    """Drop a shorter phrase when a longer one contains it and both were
    mentioned in EXACTLY the same set of postings ("prior" and "prior
    authorization" both in the same 9 postings means only the longer is
    real skill content; the shorter is a fragment of it). Comparing the
    document SETS rather than just their sizes matters: two phrases can
    reach the same count from entirely different postings, and counting
    alone would suppress one for the other with no real relationship.

    Walking every phrase's own contiguous sub-phrases and checking each
    against the table is O(phrases) rather than comparing every pair of
    phrases in the corpus, which is what keeps this fast on a real posting
    corpus with thousands of distinct candidate phrases.
    """
    suppressed: set[str] = set()
    for phrase, docs in phrase_docs.items():
        tokens = phrase.split(" ")
        if len(tokens) < 2:
            continue
        for n in range(1, len(tokens)):
            for i in range(len(tokens) - n + 1):
                sub = " ".join(tokens[i:i + n])
                if sub in phrase_docs and phrase_docs[sub] == docs:
                    suppressed.add(sub)
    return {phrase: docs for phrase, docs in phrase_docs.items() if phrase not in suppressed}


def mine_report(corpus: list[str], resume_text: str = "",
                 min_demand: int = 2,
                 employers: list[str] | None = None) -> list[KeywordDemand]:
    """Extract a skill vocabulary FROM the postings instead of scoring a
    typed one, then report it exactly like `demand_report` does.

    Candidate phrases are 1-3 word n-grams, lowercased and split on
    non-alphanumeric characters, filtered by the drop rules in `_is_dropped`
    and then by the containment rule in `_suppress_contained`. `min_demand`
    defaults to 2, dropping phrases only one posting mentions - a single
    posting's own phrasing is not a demand signal, it is that employer's
    wording.

    Pass `employers` (one name per posting, same order as `corpus`) to count
    DISTINCT EMPLOYERS rather than postings. That is the more honest signal
    and it fixes a real failure: one company's "about us" paragraph repeated
    across eight of sixteen postings scored as high demand, because eight
    postings really did contain it - but only one employer was asking. With
    employers supplied, that paragraph counts once and falls below the
    threshold, while a skill genuinely wanted by eight companies does not.

    A single word demanded by essentially every employer in the corpus
    ("healthcare" in a healthcare corpus, "transportation" in a logistics
    one) is dropped by _UBIQUITY_PCT_CEILING even after every other rule:
    a term every employer in the field mentions carries no information
    about which employer wants what, it is a restatement of the field
    itself. This bound is deliberately single-word-only and does not apply
    to multi-word phrases - a specific multi-word ask ("case management",
    "managed transportation") stays a real signal even at 100% demand,
    because a candidate's resume can still fail to evidence it.

    A corpus that is empty, or whose entire content is boilerplate/short
    fragments, returns an empty list rather than raising - same contract as
    `demand_report` for an empty vocabulary.
    """
    corpus_size = len(corpus)
    if corpus_size == 0:
        return []

    # Group each posting under the thing being counted: its employer when we
    # know it, otherwise itself.
    if employers and len(employers) == corpus_size:
        groups = [e.strip().lower() or f"posting-{i}"
                   for i, e in enumerate(employers)]
    else:
        groups = [f"posting-{i}" for i in range(corpus_size)]
    group_ids = {name: i for i, name in enumerate(dict.fromkeys(groups))}
    denominator = len(group_ids)

    phrase_docs: dict[str, set[int]] = defaultdict(set)
    for doc_index, text in enumerate(corpus):
        group = group_ids[groups[doc_index]]
        for phrase in _candidate_phrases(_tokenize(text or "")):
            phrase_docs[phrase].add(group)

    frozen = {phrase: frozenset(docs) for phrase, docs in phrase_docs.items()}
    survivors = _suppress_contained(frozen)
    resume_lower = (resume_text or "").lower()

    report: list[KeywordDemand] = []
    for phrase, docs in survivors.items():
        demand = len(docs)
        if demand < min_demand:
            continue
        pct = round(100.0 * demand / denominator, 1)
        if " " not in phrase and pct > _UBIQUITY_PCT_CEILING:
            continue
        evidenced = bool(resume_lower) and coverage.present(phrase, resume_lower)
        report.append(KeywordDemand(skill=phrase, demand=demand, pct=pct, evidenced=evidenced))

    report.sort(key=lambda r: r["demand"], reverse=True)
    return report
