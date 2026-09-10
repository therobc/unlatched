"""keywords.mine_report extracts the demand vocabulary from the postings
instead of scoring one the user typed - the whole reason it exists is that a
typed vocabulary is usually a copy of the user's own resume, which makes
every gap report come back empty. These tests protect the mining pipeline
itself: boilerplate never survives, document frequency counts POSTINGS not
raw occurrences, the containment rule only fires at equal demand, and a
corpus of nothing but boilerplate mines nothing instead of raising.
"""
from __future__ import annotations

import json

from unlatched import cli, db, keywords

RESUME = "Experienced with prior authorization and patient scheduling."

CORPUS_REPEAT = [
    "Prior authorization prior authorization prior authorization needed daily "
    "for all pharmacy claims submitted through this system each business day.",
    "This position also handles prior authorization for specialty medication "
    "requests received each week from multiple referring clinics nearby.",
]

CORPUS_CONTAINMENT = [
    "Requires prior authorization experience and strong communication skills overall.",
    "Prior authorization is a daily task, along with insurance verification duties.",
    "Handles prior authorization requests and medication reconciliation each shift.",
]

CORPUS_DIFFERING_DEMAND = [
    "Prior authorization and insurance verification are both required for this role.",
    "Prior authorization experience preferred but insurance verification is optional.",
    "This posting only mentions prior authorization, nothing about verification tasks.",
]

CORPUS_BOILERPLATE_ONLY = [
    "Equal opportunity employer. Equal employment opportunity. Affirmative "
    "action. We offer. You will. Join our team. We are looking. We are "
    "seeking. About us. About the role. About the company. Why join us. "
    "What we offer. Who we are. Apply now. How to apply. Our team. Our "
    "company. Our mission. The ideal candidate. The successful candidate.",
    "Reasonable accommodation. Veteran status. Sexual orientation. Gender "
    "identity. National origin. Protected veteran. Background check. Drug "
    "screen. At will. Job description. Job type. Job summary. Full time. "
    "Part time. Years of experience. Minimum qualifications. Preferred "
    "qualifications. Required qualifications. What you. You have. You are.",
]

# A possessive splits on its apostrophe: "bachelor's degree" tokenizes to
# "bachelor", "s", "degree" - the lone "s" must never anchor a reported
# phrase, in the middle of a 3-gram or anywhere else.
CORPUS_POSSESSIVE = [
    "Bachelor's degree required for all candidates applying to this "
    "specialty clinical coordinator position advertised today.",
    "A bachelor's degree is strongly preferred for applicants interested "
    "in this particular healthcare coordinator role.",
]

# "ensure" is already in GENERIC_SINGLE_WORDS, but the mined text uses its
# gerund "ensuring" - the inflected form was slipping through before
# GENERIC_SINGLE_WORDS was checked via _expand_inflections instead of exact
# string equality.
CORPUS_GENERIC_INFLECTION = [
    "Ensuring accurate records is essential, ensuring compliance across "
    "every single shift for clinical staff on this unit.",
    "The role involves ensuring proper documentation while ensuring "
    "patients receive appropriate timely care every shift.",
]

# "dispatching" also ends in "-ing" like "ensuring" does, but it is not an
# inflection of any GENERIC_SINGLE_WORDS entry - it is the real domain verb
# a logistics posting uses. The inflection rule must not treat every "-ing"
# word as generic, only the ones that trace back to a listed base.
# Neighboring words deliberately differ between the two postings that
# mention it so no single "dispatching X" bigram appears in both -
# otherwise the containment rule (tested separately above) would suppress
# bare "dispatching" in favor of that bigram. A third posting that never
# mentions "dispatching" keeps its demand at 2 of 3 employers, under the
# ubiquity bound (tested separately below) - at 2 of 2 it would be
# indistinguishable from a term every employer in the corpus asks for.
CORPUS_DOMAIN_GERUND = [
    "The coordinator is responsible for dispatching drivers and "
    "dispatching loads to regional carriers every single morning.",
    "Daily tasks include dispatching trucks and dispatching shipments "
    "across the assigned regional delivery network each shift.",
    "This warehouse role instead focuses on inventory counts and pallet "
    "labeling for the regional distribution center every shift.",
]

# "conduct" opens an imperative bullet in real postings ("Conduct patient
# assessments..."), which makes it - and the fragment it introduces - not a
# noun phrase. "managed transportation", by contrast, is a real employer
# ask that happens to start with an inflected form of "manage"; "manage"
# itself is deliberately left out of _FRAGMENT_EDGE_WORDS so this survives.
# The word after "managed transportation" deliberately differs between the
# two postings so no single 3-gram containing it appears in both - same
# containment-collision concern as CORPUS_DOMAIN_GERUND above.
CORPUS_FRAGMENT_EDGE = [
    "Conduct patient assessments daily and coordinate managed "
    "transportation logistics for discharge planning across the floor.",
    "Staff must conduct patient assessments while supporting managed "
    "transportation solutions for every discharge on the unit.",
]

# "ability to work" is the exact fragment named in the mining bug report:
# a generic infinitive subject ("ability") followed by "to" and a verb,
# with no noun-phrase head a candidate would ever put on a resume.
CORPUS_ABILITY_TO_WORK = [
    "Requires the ability to work independently in a fast paced clinical "
    "environment supporting patients every single day.",
    "Candidates need the ability to work independently within a "
    "demanding clinical environment supporting patients every shift.",
]

# "independently" is a real adverb, not a skill; "supply" ends in the same
# two letters but is a six-letter domain-relevant word, not an adverb, and
# must not be caught by the same suffix rule. The word after "supply"
# deliberately differs between postings for the same containment-collision
# reason as CORPUS_DOMAIN_GERUND above, and a third posting that mentions
# neither word keeps both under the ubiquity bound, same reasoning too.
CORPUS_ADVERB = [
    "Nurses must work independently and manage the supply room "
    "during every single clinical shift on this floor.",
    "Staff are expected to work independently and track the supply "
    "closet during every clinical rotation on this floor.",
    "This clinical role instead centers on direct patient education and "
    "family communication during every scheduled visit today.",
]

# "healthcare" is mentioned by every employer in this small corpus - not
# because it is a distinguishing ask, but because it names the field the
# corpus is already about. "care plans" is just as universal here but is a
# specific multi-word ask, so the ubiquity bound must leave it alone.
CORPUS_UBIQUITOUS = [
    "This healthcare organization is hiring for a role focused on care "
    "plans and coordinating services for patients across the region.",
    "Our healthcare organization is hiring for a role focused on care "
    "plans and coordinating outreach for patients across the region.",
]


def test_boilerplate_phrases_are_excluded():
    report = keywords.mine_report(CORPUS_BOILERPLATE_ONLY, RESUME, min_demand=1)
    mined = {r["skill"] for r in report}
    assert "equal opportunity" not in mined
    assert "we offer" not in mined
    assert "join our team" not in mined
    assert "you will" not in mined


def test_pure_boilerplate_corpus_mines_nothing():
    report = keywords.mine_report(CORPUS_BOILERPLATE_ONLY, RESUME, min_demand=1)
    assert report == []


def test_document_frequency_counts_postings_not_occurrences():
    # "prior authorization" occurs 4 times total (3 in doc 0, 1 in doc 1) but
    # only 2 postings mention it - demand must be 2, the posting count, not
    # the raw occurrence count.
    report = keywords.mine_report(CORPUS_REPEAT, RESUME, min_demand=1)
    by_phrase = {r["skill"]: r for r in report}
    assert by_phrase["prior authorization"]["demand"] == 2


def test_containment_suppresses_shorter_phrase_at_equal_demand():
    # "prior" and "prior authorization" both appear in all three documents -
    # the shorter fragment must be dropped in favor of the longer phrase.
    report = keywords.mine_report(CORPUS_CONTAINMENT, RESUME, min_demand=1)
    mined = {r["skill"] for r in report}
    assert "prior authorization" in mined
    assert "prior" not in mined


def test_containment_keeps_shorter_phrase_when_demand_differs():
    # "prior authorization" is mentioned in all 3 postings; "insurance
    # verification" only in the first 2. Neither contains the other, so
    # both must survive independently regardless of the demand gap.
    report = keywords.mine_report(CORPUS_DIFFERING_DEMAND, RESUME, min_demand=1)
    by_phrase = {r["skill"]: r for r in report}
    assert by_phrase["prior authorization"]["demand"] == 3
    assert by_phrase["insurance verification"]["demand"] == 2


def test_min_demand_filters_low_frequency_phrases():
    report_default = keywords.mine_report(CORPUS_DIFFERING_DEMAND, RESUME)
    mined_default = {r["skill"] for r in report_default}
    assert "insurance verification" in mined_default  # demand 2 clears default min_demand=2

    report_strict = keywords.mine_report(CORPUS_DIFFERING_DEMAND, RESUME, min_demand=3)
    mined_strict = {r["skill"] for r in report_strict}
    assert "insurance verification" not in mined_strict  # demand 2 < 3, dropped
    assert "prior authorization" in mined_strict  # demand 3 still clears


def test_evidenced_flips_against_the_resume():
    report = keywords.mine_report(CORPUS_CONTAINMENT, RESUME, min_demand=1)
    by_phrase = {r["skill"]: r for r in report}
    assert by_phrase["prior authorization"]["evidenced"] is True
    assert "communication skills" not in by_phrase or \
        by_phrase["communication skills"]["evidenced"] is False


def test_evidenced_is_false_with_no_resume_text():
    report = keywords.mine_report(CORPUS_CONTAINMENT, "", min_demand=1)
    assert all(r["evidenced"] is False for r in report)


def test_empty_corpus_returns_empty_report():
    assert keywords.mine_report([], RESUME) == []


def test_ordering_is_demand_descending():
    report = keywords.mine_report(CORPUS_DIFFERING_DEMAND, RESUME, min_demand=1)
    demands = [r["demand"] for r in report]
    assert demands == sorted(demands, reverse=True)


def test_cli_mine_json_returns_ranked_array_on_seeded_corpus(tmp_path, capsys):
    home = tmp_path / "home"
    con = db.connect(home)
    for i, description in enumerate(CORPUS_CONTAINMENT):
        db.upsert_job(con, f"greenhouse:{i}",
                       {"title": "Care Coordinator", "description": description,
                        "qualified": 1})
    con.close()
    resume = tmp_path / "resume.txt"
    resume.write_text(RESUME, encoding="utf-8")
    assert cli.main(["--home", str(home), "config", "set",
                      "resume_path", str(resume)]) == 0
    capsys.readouterr()

    rc = cli.main(["--home", str(home), "keywords", "--mine", "--min-demand", "1", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert len(payload) > 0
    assert payload[0]["demand"] >= payload[-1]["demand"]
    assert "prior authorization" in {row["skill"] for row in payload}


def test_possessive_split_never_anchors_a_phrase():
    # "bachelor's degree" tokenizes to "bachelor", "s", "degree" - the lone
    # "s" must never survive as its own reported term or as part of one,
    # in any position, not just at the edges of the n-gram window.
    report = keywords.mine_report(CORPUS_POSSESSIVE, RESUME)
    mined = {r["skill"] for r in report}
    assert "s degree" not in mined
    assert "bachelor s" not in mined
    assert all(len(tok) > 1 for skill in mined for tok in skill.split(" "))


def test_generic_word_gate_catches_inflected_forms():
    # "ensure" is already in GENERIC_SINGLE_WORDS; the postings use
    # "ensuring". Before inflection-aware matching, the exact-string check
    # let every conjugation except the listed one straight through.
    report = keywords.mine_report(CORPUS_GENERIC_INFLECTION, RESUME)
    mined = {r["skill"] for r in report}
    assert "ensuring" not in mined
    assert "ensures" not in mined


def test_generic_word_gate_does_not_catch_unrelated_domain_gerund():
    # Negative case for the same rule: "dispatching" also ends in "-ing"
    # but is not an inflection of anything in GENERIC_SINGLE_WORDS - it is
    # the real domain verb a logistics posting uses, and must survive.
    report = keywords.mine_report(CORPUS_DOMAIN_GERUND, RESUME)
    mined = {r["skill"] for r in report}
    assert "dispatching" in mined


def test_fragment_edge_words_drop_verb_opened_phrases():
    # "conduct" only ever opens an imperative bullet in real postings
    # ("Conduct patient assessments...") - a phrase built around it is a
    # fragment, not a noun phrase a candidate would ever put on a resume.
    report = keywords.mine_report(CORPUS_FRAGMENT_EDGE, RESUME)
    mined = {r["skill"] for r in report}
    assert "conduct patient" not in mined
    assert "conduct" not in mined


def test_fragment_edge_words_do_not_catch_managed_transportation():
    # Negative case: "managed" is an inflection of "manage", which IS a
    # generic word, but "manage" itself was deliberately left out of
    # _FRAGMENT_EDGE_WORDS because "managed transportation" is a real,
    # specific employer ask, not a verb fragment.
    report = keywords.mine_report(CORPUS_FRAGMENT_EDGE, RESUME)
    mined = {r["skill"] for r in report}
    assert "managed transportation" in mined


def test_ability_to_work_fragment_is_dropped():
    # The exact fragment named in the mining bug report: a generic
    # infinitive subject ("ability") plus "to" plus a verb, with no noun
    # phrase a candidate would recognize as "the skill" being asked for.
    report = keywords.mine_report(CORPUS_ABILITY_TO_WORK, RESUME)
    mined = {r["skill"] for r in report}
    assert "ability to work" not in mined
    assert "ability" not in mined


def test_adverb_suffix_rule_drops_long_ly_words():
    report = keywords.mine_report(CORPUS_ADVERB, RESUME)
    mined = {r["skill"] for r in report}
    assert "independently" not in mined


def test_adverb_suffix_rule_does_not_catch_short_domain_words():
    # Negative case: "supply" ends in the same two letters as
    # "independently" but is six letters long and a real domain word, not
    # an adverb - the length floor exists specifically to protect it.
    report = keywords.mine_report(CORPUS_ADVERB, RESUME)
    mined = {r["skill"] for r in report}
    assert "supply" in mined


def test_ubiquity_bound_drops_universal_single_word():
    # "healthcare" is mentioned by every employer in this corpus - not
    # because it distinguishes one employer's ask from another's, but
    # because it names the field the whole corpus is already about.
    report = keywords.mine_report(CORPUS_UBIQUITOUS, RESUME)
    mined = {r["skill"] for r in report}
    assert "healthcare" not in mined


def test_ubiquity_bound_does_not_apply_to_multi_word_phrases():
    # "care plans" is just as universal in this corpus as "healthcare" is,
    # but it is a specific multi-word ask - a candidate's resume can still
    # fail to evidence it, so the ubiquity bound must leave it alone.
    report = keywords.mine_report(CORPUS_UBIQUITOUS, RESUME)
    mined = {r["skill"] for r in report}
    assert "care plans" in mined


def test_contraction_remnants_never_anchor_a_phrase():
    # "we're looking" tokenizes to "we", "re", "looking" - the two-letter
    # "re" left behind by the apostrophe must never anchor a phrase, the
    # same way the single-character possessive remnant cannot.
    corpus = [
        "We're looking for a coordinator to manage daily inbound and "
        "outbound freight tenders for several regional shipper accounts.",
        "We're looking for a coordinator to manage daily inbound and "
        "outbound freight tenders for several regional carrier accounts.",
    ]
    report = keywords.mine_report(corpus, RESUME)
    mined = {r["skill"] for r in report}
    assert not any(tok in ("re", "ve", "ll") for skill in mined for tok in skill.split(" "))
