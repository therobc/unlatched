"""Clearance and public trust screening, built against the live USAJOBS
vocabulary rather than a guess at it.

Every literal in VOCABULARY below was observed in a 400-posting sample from
data.usajobs.gov. That matters for two of them in particular:

  * "Not Required" is the SINGLE most common value of SecurityClearance.
    Reading it as a requirement would invert the filter and discard exactly
    the postings a clearance-free candidate wants.
  * "Noncritical-Sensitive (NCS)/Moderate Risk" shares the words "Moderate
    Risk" with the public trust tier but is a national-security position.
    The word "Sensitive" is the only thing separating the two ladders.
"""
from __future__ import annotations

from typing import Any

import pytest

from unlatched import config, requirements, screen

# screen_job returns `qualified` as 0/1 (it goes straight into a SQLite
# column), so these assert truthiness rather than identity with a bool.

# (field line, expects_clearance, expects_public_trust)
VOCABULARY = [
    ("Security Clearance: Not Required", False, False),
    ("Security Clearance: Secret", True, False),
    ("Security Clearance: Top Secret", True, False),
    ("Security Clearance: Sensitive Compartmented Information", True, False),
    ("Security Clearance: Q Access Authorization", True, False),
    ("Position Sensitivity: None", False, False),
    ("Position Sensitivity: Non-sensitive (NS)/Low Risk", False, False),
    ("Position Sensitivity: Moderate Risk (MR)", False, True),
    ("Position Sensitivity: High Risk (HR)", False, True),
    ("Position Sensitivity: Noncritical-Sensitive (NCS)/Moderate Risk", True, False),
    ("Position Sensitivity: Special-Sensitive (SS)/High Risk", True, False),
    ("Position Sensitivity: Critical-Sensitive (CS)/High Risk", True, False),
]


@pytest.mark.parametrize(("line", "clearance", "trust"), VOCABULARY)
def test_live_usajobs_vocabulary_classifies_correctly(
        line: str, clearance: bool, trust: bool):
    assert bool(requirements.clearance(line)[0]) is clearance
    assert bool(requirements.public_trust(line)[0]) is trust


def test_the_two_ladders_never_double_count_one_requirement():
    """A Sensitive tier is a clearance position. Reporting it as public trust
    as well would show one requirement to the reader as two.
    """
    for line, _c, _t in VOCABULARY:
        c = requirements.clearance(line)[0]
        t = requirements.public_trust(line)[0]
        assert not (c and t), f"{line} classified as both"


def test_plain_prose_forms_still_match():
    """The structured fields only exist on USAJOBS; every other source states
    this in prose, which is what the regexes originally targeted.
    """
    assert requirements.clearance("Must hold an active Secret clearance.")[0]
    assert requirements.public_trust("This is a Moderate Risk Public Trust position.")[0]
    assert requirements.clearance("A security clearance is not required.")[0] is None


# ------------------------------------------------------------- screening ---

class _Job:
    def __init__(self, description: str):
        self.title = "Program Analyst"
        self.location = "Washington, DC"
        self.description = description
        self.url = ""


def _cfg(**profile: Any) -> dict[str, Any]:
    cfg = config.defaults()
    cfg["profile"].update(profile)
    return cfg


def test_unconfigured_vetting_never_filters():
    """None means the question was never answered, so it must not act like a
    "no" - the same not-stated-vs-not-required rule the rest of the profile
    follows.
    """
    result = screen.screen_job(_Job("Security Clearance: Top Secret"), _cfg())
    assert result["qualified"]


def test_clearance_disqualifies_and_says_why():
    result = screen.screen_job(
        _Job("Security Clearance: Secret"), _cfg(clearance_ok=False))
    assert not result["qualified"]
    assert "clearance" in result["screen_reasons"]


def test_public_trust_disqualifies_independently_of_clearance():
    """Someone may accept public trust but refuse a clearance, so the flags
    have to act separately.
    """
    posting = _Job("Position Sensitivity: Moderate Risk (MR)")
    assert screen.screen_job(posting, _cfg(clearance_ok=False))["qualified"]
    assert not screen.screen_job(posting, _cfg(public_trust_ok=False))["qualified"]


def test_a_clean_federal_posting_survives_both_filters():
    clean = _Job("Security Clearance: Not Required\n\n"
                 "Position Sensitivity: Non-sensitive (NS)/Low Risk")
    result = screen.screen_job(clean, _cfg(clearance_ok=False, public_trust_ok=False))
    assert result["qualified"]


def test_both_fields_are_consulted_because_they_disagree():
    """Measured: 50 of 400 postings carried a clean PositionSensitivitiy AND
    SecurityClearance "Secret". Either field alone passes those through.
    """
    contradictory = _Job("Security Clearance: Secret\n\n"
                         "Position Sensitivity: Non-sensitive (NS)/Low Risk")
    result = screen.screen_job(contradictory, _cfg(clearance_ok=False))
    assert not result["qualified"]


def test_clearance_other_is_not_read_as_a_requirement():
    """"Other" is the agency choosing a value off the standard list. Measured
    over 400 live postings it appeared 91 times, 67 alongside a CLEAN
    PositionSensitivitiy - treating it as a requirement discarded 8 of the 27
 genuinely remote federal jobs, which is the whole pool such a search has.
    """
    posting = _Job("Clearance listed as: Other (unspecified)\n\n"
                   "Position Sensitivity: Non-sensitive (NS)/Low Risk")
    result = screen.screen_job(posting, _cfg(clearance_ok=False, public_trust_ok=False))
    assert result["qualified"]


def test_other_still_defers_to_sensitivity_when_that_field_is_dirty():
    posting = _Job("Clearance listed as: Other (unspecified)\n\n"
                   "Position Sensitivity: High Risk (HR)")
    assert not screen.screen_job(posting, _cfg(public_trust_ok=False))["qualified"]


def test_vetting_flags_validate_as_tristate():
    cfg = config.defaults()
    cfg["profile"]["clearance_ok"] = "maybe"
    assert any("clearance_ok" in p for p in config.validate(cfg))
    cfg["profile"]["clearance_ok"] = False
    assert not [p for p in config.validate(cfg) if "clearance_ok" in p]
