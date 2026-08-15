"""What a posting demands, compressed to something that fits a table row.

The point is ruling a row out WITHOUT opening it. So the properties that
matter are that it never claims a requirement the posting did not state, and
that it does not turn a soft requirement into a hard-looking one - "BS" and
"BS or exp" send a reader to two different decisions.
"""
from __future__ import annotations

from unlatched import requirements


def summarise(text: str) -> str:
    return requirements.summary(requirements.extract(text))


def test_a_posting_that_states_nothing_summarises_to_nothing():
    """Silence is not "no requirements" - it is silence, and inventing
    "none required" would be a claim the posting never made.
    """
    assert summarise("We are hiring a support analyst. Come join a great team.") == ""


def test_years_and_degree_are_both_carried():
    out = summarise("Requires 5 years of experience and a Bachelor's degree.")
    assert "5+ yrs" in out
    assert "BS" in out


def test_a_preferred_degree_is_not_shown_as_a_wall():
    out = summarise("Bachelor's degree preferred, though not required.")
    assert "pref" in out


def test_a_degree_with_an_equivalence_says_so():
    """"BS" alone would read as a hard requirement to someone without one,
    when the posting explicitly opened the door.
    """
    out = summarise("Bachelor's degree or equivalent work experience required.")
    assert "or exp" in out


def test_a_clearance_is_named():
    out = summarise("Must hold an active Secret security clearance. 2 years experience.")
    assert "clearance" in out


def test_travel_is_carried_with_its_number_when_stated():
    out = summarise("This role requires up to 50% travel. 4 years of experience.")
    assert "travel 50%" in out


def test_licenses_appear_and_are_capped():
    """A row has finite width; three licences would push the years off the
    end, and years is the more common disqualifier.
    """
    out = summarise(
        "Requires a valid CDL, a forklift certification, an OSHA 30 card "
        "and a TWIC card. 2 years of experience.")
    assert "2+ yrs" in out
    assert out.count(",") <= 3


def test_the_summary_stays_short_enough_for_a_column():
    out = summarise(
        "Requires 10 years of experience, a Master's degree, an active Top Secret "
        "clearance, a valid CDL, up to 75% travel, night shift work, and "
        "supervision of a team of twelve.")
    assert len(out) < 90, out


def test_the_employers_own_age_is_not_a_requirement():
    """Found live: "For over 20 years, Smartsheet has empowered teams..." was
    read as a 20-year experience requirement. It reaches compare(), so it can
    flag a row against a candidate who was never short of anything.
    """
    assert requirements.years_required(
        "For over 20 years, Smartsheet has empowered teams to manage work.")[0] is None


def test_a_real_requirement_survives_a_company_boast_later_in_the_posting():
    """The narrow-window rule, asserted. A guard checked over a wide window
    swallowed the real requirement along with the boast.
    """
    years, _ = requirements.years_required(
        "At least 2 years experience. For over 20 years, Acme has led the market.")
    assert years == 2


def test_level_names_do_not_leak_underscores():
    """"high_school" and "on_call" reached the column as raw enum values."""
    out = summarise("High school diploma or GED required. 1 year of experience.")
    assert "_" not in out
    assert "HS" in out
