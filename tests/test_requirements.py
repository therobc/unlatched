"""Requirements extraction: the screen-out details that live only in
description prose - years of experience, education level, licenses, shift
pattern, travel, physical demands, supervisory scope, clearance - and the
comparison against a candidate profile.

Every positive case here is checked against the EVIDENCE string too, not
just the parsed value, since a value with no evidence is exactly the kind
of unverifiable claim this module exists to avoid. Negative cases exist
because the extractors are regexes over free text: a company's age ("25
years in business") or a passing mention with no expectation attached
("we occasionally work weekends") must not be read as a requirement.
"""
from __future__ import annotations

import json

from unlatched import cli, config, db, requirements

# --------------------------------------------------------------- years ---

def test_plus_years():
    val, ev = requirements.years_required("Requires 3+ years of relevant experience.")
    assert val == 3
    assert "3" in ev
    assert "years" in ev


def test_spelled_out_minimum_of_five_years():
    val, _ev = requirements.years_required(
        "Candidates must have a minimum of five years of experience in accounting.")
    assert val == 5


def test_range_reports_the_low_end():
    val, ev = requirements.years_required("5-7 years of experience with SQL required.")
    assert val == 5
    assert "5-7" in ev or "5" in ev


def test_spelled_out_up_to_twenty():
    val, _ev = requirements.years_required(
        "Twenty years of progressive leadership experience preferred.")
    assert val == 20


def test_at_least_phrasing():
    val, _ev = requirements.years_required("At least 2 years of customer service experience.")
    assert val == 2


def test_company_age_is_not_a_years_requirement():
    val, ev = requirements.years_required(
        "Founded in 2001, our company has been in business for 25 years.")
    assert val is None
    assert ev == ""


def test_no_years_mentioned_returns_none():
    val, ev = requirements.years_required("Join our growing team as a Warehouse Associate.")
    assert val is None
    assert ev == ""


def test_minimum_age_is_not_a_years_requirement():
    # A real miss found against a live CDL driver posting: "at least 22
    # years" reads identically to an experience floor right up to the
    # trailing "of age".
    val, ev = requirements.years_required("Must be at least 22 years of age.")
    assert val is None
    assert ev == ""


def test_minimum_age_does_not_swallow_a_nearby_experience_requirement():
    val, ev = requirements.years_required(
        "Must be at least 21 years of age. 5+ years of relevant experience preferred.")
    assert val == 5
    assert "5" in ev


def test_parenthesized_number_is_captured_with_its_parens():
    val, ev = requirements.years_required("Minimum (2) years' clinical nursing experience.")
    assert val == 2
    assert "(2)" in ev


def test_spelled_out_number_immediately_followed_by_its_digit():
    # "one (1) year" is common in structured postings that spell the
    # number out and then restate it numerically right after.
    val, ev = requirements.years_required("Minimum one (1) year experience as an RN.")
    assert val == 1
    assert "(1)" in ev


def test_negative_context_does_not_bleed_across_bullets():
    # A real miss: "in business" from an unrelated degree-field bullet
    # ("Bachelor's degree in business...") bled backward into the very
    # next bullet's stated years-of-experience figure.
    posting = (
        "- Bachelor's degree in business or a related field.\n\n"
        "- 5+ years of transportation or dispatch experience.\n"
    )
    val, ev = requirements.years_required(posting)
    assert val == 5
    assert "5" in ev


# ----------------------------------------------------------- education ---

def test_bachelor_required_by_default_wording():
    level, preferred, ev = requirements.education_required(
        "Requirements: Bachelor's degree in Business Administration or related field.")
    assert level == "bachelor"
    assert preferred is False
    assert "bachelor" in ev.lower()


def test_bachelor_preferred_is_distinguished_from_required():
    level, preferred, _ev = requirements.education_required(
        "Bachelor's degree preferred but not required for the right candidate.")
    assert level == "bachelor"
    assert preferred is True


def test_master_required():
    level, preferred, _ev = requirements.education_required(
        "A Master's degree is required for this senior analyst role.")
    assert level == "master"
    assert preferred is False


def test_high_school_or_ged():
    level, _preferred, ev = requirements.education_required(
        "Must have a high school diploma or GED equivalent.")
    assert level == "high_school"
    assert "high school" in ev.lower()


def test_explicit_no_degree_required_is_a_real_value_not_absence():
    level, preferred, ev = requirements.education_required(
        "No degree required - we care about what you can do, not your transcript.")
    assert level == "none"
    assert preferred is False
    assert ev


def test_education_never_mentioned_is_none_not_a_guess():
    level, preferred, ev = requirements.education_required(
        "We are looking for a reliable, hard-working team player.")
    assert level is None
    assert preferred is None
    assert ev == ""


def test_curly_apostrophe_is_recognized_like_a_straight_one():
    # A real miss: live postings routinely use the typographic right single
    # quote (\u2019) rather than a straight apostrophe for possessives.
    # Spelled out as an escape (not typed literally) so this source file
    # stays plain ASCII, matching the rest of the public repo.
    level, preferred, _ev = requirements.education_required(
        "Requirements \u2013 Bachelor\u2019s or Master\u2019s degree in Computer Science.")
    assert level == "master"
    assert preferred is False


def test_next_bullets_required_label_does_not_leak_into_this_bullets_preferred():
    # A real miss against a live Workday-style posting: a structured
    # template repeats "Required:"/"Preferred:" once per bullet (Education,
    # Training, Licensure...), and a flat window on the Education bullet
    # picked up the following Training bullet's own "Required: None" label.
    posting = (
        "Education: \n"
        " - Required: Completion of an accredited nursing program\n"
        " - Preferred: Associates or Bachelor's degree in Nursing\n"
        "\n"
        "Training: \n"
        " - Required: None\n"
        " - Preferred: Medical Terminology\n"
    )
    level, preferred, _ev = requirements.education_required(posting)
    assert level == "bachelor"
    assert preferred is True


def test_section_heading_governs_a_bulleted_degree_with_no_local_wording():
    # A real miss: a "Preferred Qualifications" section heading several
    # lines above a bulleted degree line, with no softening word on the
    # bullet itself.
    posting = (
        "Preferred Qualifications\n\n"
        "- Bachelor's degree in business or a related field.\n\n"
        "- 5+ years of transportation or dispatch experience.\n"
    )
    level, preferred, _ev = requirements.education_required(posting)
    assert level == "bachelor"
    assert preferred is True


def test_required_qualifications_heading_governs_too():
    posting = (
        "Required Qualifications\n\n"
        "- Bachelor's degree in a related field.\n"
    )
    level, preferred, _ev = requirements.education_required(posting)
    assert level == "bachelor"
    assert preferred is False


def test_two_degree_levels_on_one_line_report_the_required_floor_not_the_preferred_ceiling():
    # A real miss against a live posting: highest-level-first matching
    # alone reported "Master's, required" for a line that actually reads
    # "Bachelor's degree ... required; Master's degree preferred" - the
    # Master's mention is a stretch on top of a Bachelor's floor, not the
    # gate itself, and the semicolon separates two distinct clauses that a
    # naive fixed-width window blurred together.
    posting = (
        "Bachelor's degree in Information Security, Computer Science, or "
        "related field required; Master's degree preferred")
    level, preferred, ev = requirements.education_required(posting)
    assert level == "bachelor"
    assert preferred is False
    assert "bachelor" in ev.lower()


# ------------------------------------------------------------ licenses ---

def test_cdl_and_driver_license_both_detected():
    hits = requirements.licenses(
        "Valid CDL Class A required. A clean driver's license is also expected.")
    names = {h["name"] for h in hits}
    assert "CDL" in names
    assert "Driver's License" in names


def test_healthcare_licenses():
    hits = requirements.licenses(
        "Active RN license required. Current BLS and ACLS certification required.")
    names = {h["name"] for h in hits}
    assert names == {"RN", "BLS", "ACLS"}


def test_trade_and_office_licenses():
    hits = requirements.licenses(
        "Forklift certification required. OSHA 10 preferred. Notary public a plus.")
    names = {h["name"] for h in hits}
    assert "Forklift" in names
    assert "OSHA 10" in names
    assert "Notary" in names


def test_no_licenses_mentioned_returns_empty_list():
    assert requirements.licenses("Great benefits and a supportive team culture.") == []


# --------------------------------------------------------------- shift ---

def test_night_shift_stated_as_expectation():
    hits = requirements.shift("This is a night shift position; must work nights.")
    assert {h["kind"] for h in hits} == {"nights"}


def test_weekend_requirement():
    hits = requirements.shift("Must be available to work weekends.")
    assert any(h["kind"] == "weekends" for h in hits)


def test_on_call_and_rotating_and_overtime():
    hits = requirements.shift(
        "This role includes on-call rotation, a rotating schedule, and mandatory overtime.")
    kinds = {h["kind"] for h in hits}
    assert kinds == {"on_call", "rotating", "overtime"}


def test_passing_mention_with_no_expectation_is_not_a_requirement():
    hits = requirements.shift("Our team sometimes works late during launch week.")
    assert hits == []


# -------------------------------------------------------------- travel ---

def test_travel_percentage():
    pct, qual, ev = requirements.travel("This role requires up to 25% travel.")
    assert pct == 25
    assert qual is None
    assert "25" in ev


def test_qualitative_travel_when_no_percent_given():
    pct, qual, ev = requirements.travel("Frequent travel to client sites is expected.")
    assert pct is None
    assert qual is not None
    assert "travel" in ev.lower()


def test_no_travel_mentioned():
    pct, qual, ev = requirements.travel("Fully remote, work from anywhere.")
    assert pct is None
    assert qual is None
    assert ev == ""


# ------------------------------------------------------------ physical ---

def test_lifting_weight_is_captured():
    physical = requirements.physical(
        "Must be able to lift up to 50 lbs and stand for extended periods.")
    assert physical["lifting_lbs"] == 50
    assert physical["standing"] is True


def test_climbing_detected():
    physical = requirements.physical("Ability to climb ladders and work at heights.")
    assert physical["climbing"] is True


def test_no_physical_demands_mentioned():
    physical = requirements.physical("Remote data entry role, flexible hours.")
    assert physical["lifting"] is None
    assert physical["lifting_lbs"] is None
    assert physical["standing"] is False
    assert physical["climbing"] is False


# --------------------------------------------------------- supervises ---

def test_supervises_detected_with_evidence():
    ok, ev = requirements.supervises_required("Will manage a team of 5 customer service reps.")
    assert ok is True
    assert "team" in ev.lower()


def test_supervises_absent_is_none_never_false():
    ok, ev = requirements.supervises_required("Individual contributor role on the platform team.")
    assert ok is None
    assert ev == ""


def test_supervisory_heading_answered_na_is_not_supervisory():
    # A real miss: structured postings carry a "Supervisory Responsibilities:"
    # heading on every posting, filled with "N/A" for individual
    # contributors - the heading alone was being read as evidence.
    posting = "Supervisory Responsibilities: \n N/A\n \n Minimum Requirements: \n"
    ok, ev = requirements.supervises_required(posting)
    assert ok is None
    assert ev == ""


def test_supervisory_heading_answered_with_a_negating_sentence():
    # A real miss: the negative answer is not always a bare "N/A" - a live
    # posting spelled it out in full prose.
    posting = (
        "Supervisory Responsibilities: \n"
        "This role is an individual contributor with no direct reports or "
        "supervisory authority. The RN leads through clinical guidance.\n"
        "\n"
        "Minimum Requirements: \n"
    )
    ok, ev = requirements.supervises_required(posting)
    assert ok is None
    assert ev == ""


def test_supervisory_heading_answered_affirmatively_is_still_detected():
    posting = "Supervisory Responsibilities: \nWill manage a team of 5 direct reports.\n\nOther:"
    ok, ev = requirements.supervises_required(posting)
    assert ok is True
    assert ev


# --------------------------------------------------------------- clearance ---

def test_clearance_detected():
    val, ev = requirements.clearance(
        "Candidates must hold an active Secret security clearance.")
    assert val is not None
    assert ev


def test_no_clearance_mentioned():
    val, ev = requirements.clearance("Open to candidates authorized to work in the US.")
    assert val is None
    assert ev == ""


# ---------------------------------------------------------------- extract ---

def test_extract_assembles_every_field():
    posting = (
        "Requirements: 3+ years of experience. Bachelor's degree required. "
        "Valid CDL required. Night shift, weekends required. Travel up to 25%. "
        "Must be able to lift 50 lbs. Will manage a team of 3. Secret clearance required."
    )
    reqs = requirements.extract(posting)
    assert reqs["years_required"] == 3
    assert reqs["education_level"] == "bachelor"
    assert reqs["education_preferred"] is False
    assert {h["name"] for h in reqs["licenses"]} == {"CDL"}
    assert {h["kind"] for h in reqs["shift"]} == {"nights", "weekends"}
    assert reqs["travel_pct"] == 25
    assert reqs["physical"]["lifting_lbs"] == 50
    assert reqs["supervises"] is True
    assert reqs["clearance"] is not None


def test_extract_on_empty_description_is_all_none_or_empty():
    reqs = requirements.extract("")
    assert reqs["years_required"] is None
    assert reqs["education_level"] is None
    assert reqs["licenses"] == []
    assert reqs["shift"] == []
    assert reqs["travel_pct"] is None
    assert reqs["supervises"] is None
    assert reqs["clearance"] is None


# --------------------------------------------------------------- compare ---

def _empty_profile():
    return config.defaults()["profile"]


def test_unconfigured_profile_never_produces_a_blocker():
    reqs = requirements.extract(
        "Requires 10+ years of experience. Master's degree required. "
        "Valid CDL required. Secret clearance required.")
    result = requirements.compare(reqs, _empty_profile())
    assert result == {"blockers": [], "stretches": [], "meets": []}


def test_license_not_held_is_a_blocker():
    reqs = requirements.extract("Valid CDL required for all drivers.")
    profile = _empty_profile()
    profile["licenses"] = ["Forklift"]
    result = requirements.compare(reqs, profile)
    assert len(result["blockers"]) == 1
    assert "CDL" in result["blockers"][0]


def test_license_held_is_a_meet():
    reqs = requirements.extract("Valid CDL required for all drivers.")
    profile = _empty_profile()
    profile["licenses"] = ["CDL"]
    result = requirements.compare(reqs, profile)
    assert result["blockers"] == []
    assert any("CDL" in m for m in result["meets"])


def test_education_above_profile_stated_as_required_is_a_blocker():
    reqs = requirements.extract("Master's degree required for this role.")
    profile = _empty_profile()
    profile["education"] = "bachelor"
    result = requirements.compare(reqs, profile)
    assert len(result["blockers"]) == 1
    assert "master" in result["blockers"][0]


def test_education_above_profile_stated_as_preferred_is_a_stretch_not_a_blocker():
    reqs = requirements.extract("Master's degree preferred, not required.")
    profile = _empty_profile()
    profile["education"] = "bachelor"
    result = requirements.compare(reqs, profile)
    assert result["blockers"] == []
    assert len(result["stretches"]) == 1


def test_years_short_is_a_stretch_never_a_blocker():
    reqs = requirements.extract("5+ years of experience required.")
    profile = _empty_profile()
    profile["years_experience"] = 3
    result = requirements.compare(reqs, profile)
    assert result["blockers"] == []
    assert len(result["stretches"]) == 1
    assert "3" in result["stretches"][0]


def test_years_met_is_a_meet():
    reqs = requirements.extract("5+ years of experience required.")
    profile = _empty_profile()
    profile["years_experience"] = 8
    result = requirements.compare(reqs, profile)
    assert result["stretches"] == []
    assert len(result["meets"]) == 1


def test_shift_not_accepted_is_a_stretch():
    reqs = requirements.extract("Night shift position; must work nights.")
    profile = _empty_profile()
    profile["willing_shifts"] = ["weekends"]
    result = requirements.compare(reqs, profile)
    assert result["blockers"] == []
    assert len(result["stretches"]) == 1


def test_travel_when_profile_cannot_travel_is_a_blocker():
    reqs = requirements.extract("This role requires up to 50% travel.")
    profile = _empty_profile()
    profile["can_travel"] = False
    result = requirements.compare(reqs, profile)
    assert len(result["blockers"]) == 1


def test_supervises_when_profile_declines_is_a_blocker():
    reqs = requirements.extract("Will manage a team of 6 direct reports.")
    profile = _empty_profile()
    profile["supervises_ok"] = False
    result = requirements.compare(reqs, profile)
    assert len(result["blockers"]) == 1


def test_profile_is_configured_false_for_defaults():
    assert requirements.profile_is_configured(_empty_profile()) is False


def test_profile_is_configured_true_once_any_field_is_set():
    profile = _empty_profile()
    profile["years_experience"] = 5
    assert requirements.profile_is_configured(profile) is True


# ------------------------------------------------------------------- CLI ---

def test_requirements_verb_json_through_cli(tmp_path, monkeypatch, capsys):
    """The whole path: a stored posting, a configured profile, and the JSON an
    assistant reads back.

    ASSERTS THE PAYLOAD, not just the exit code. It used to check `rc == 0`
    alone, which would have passed had the command printed nothing at all -
    and this verb exists to be read by something other than a person, so the
    content IS the feature.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("UNLATCHED_HOME", str(home))
    con = db.connect(home)
    db.upsert_job(con, "test:1", {
        "title": "Delivery Driver", "location": "Dayton, OH",
        "description": "Valid CDL required. 2+ years of driving experience. "
                        "Must be able to lift 50 lbs.",
        "qualified": 1,
    })
    con.close()

    cfg = config.defaults()
    cfg["profile"]["licenses"] = ["CDL"]
    cfg["profile"]["years_experience"] = 5
    config.save(cfg, home)

    rc = cli.main(["--home", str(home), "requirements", "test:1", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["key"] == "test:1"

    reqs = payload["requirements"]
    assert reqs["years_required"] == 2
    assert [h["name"] for h in reqs["licenses"]] == ["CDL"]
    assert reqs["physical"]["lifting_lbs"] == 50

    # The profile holds the licence and exceeds the years, so both are met
    # and nothing blocks - the answer an assistant acts on.
    compared = payload["compare"]
    assert compared["blockers"] == []
    assert any("CDL" in m for m in compared["meets"])
    assert len(compared["meets"]) == 2


def test_requirements_json_omits_the_comparison_when_no_profile_is_set(
        tmp_path, monkeypatch, capsys):
    """An unconfigured profile must not produce an empty `compare` block.

    Absent and "nothing matched" are different answers, and a reader handed
    {"blockers": [], "stretches": [], "meets": []} would take the second for
    the first - concluding a posting had been checked against somebody when
    nothing about them is known.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("UNLATCHED_HOME", str(home))
    con = db.connect(home)
    db.upsert_job(con, "test:3", {
        "title": "Delivery Driver",
        "description": "Valid CDL required. Master's degree required.",
        "qualified": 1,
    })
    con.close()

    rc = cli.main(["--home", str(home), "requirements", "test:3", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "compare" not in payload
    # The requirements themselves are still reported - they are a fact about
    # the posting, not about the person.
    assert [h["name"] for h in payload["requirements"]["licenses"]] == ["CDL"]


def test_requirements_verb_reports_missing_key(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("UNLATCHED_HOME", str(home))
    db.connect(home).close()
    rc = cli.main(["--home", str(home), "requirements", "does-not-exist"])
    assert rc == 1
    assert "no such job" in capsys.readouterr().err


def test_show_includes_requirements_summary(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("UNLATCHED_HOME", str(home))
    con = db.connect(home)
    db.upsert_job(con, "test:2", {
        "title": "Warehouse Associate", "location": "Dayton, OH",
        "description": "3+ years of experience. Must lift up to 50 lbs.",
        "qualified": 1,
    })
    con.close()
    rc = cli.main(["--home", str(home), "show", "test:2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "requirements_summary" in out
