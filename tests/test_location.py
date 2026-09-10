"""Commute filtering: only asked about postings that are not remote.

A carpenter cannot take a job three states away, and before this existed
every posting in the country qualified for them. The rules that matter are
that a bare city name is not enough (city names repeat), and that an
employer based nearby who sends crews out should still be visible to
someone willing to travel.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unlatched import config, location, screen

NEAR = ["Dayton, OH", "Springfield, OH", "Xenia, OH", "Powell, OH"]


@pytest.mark.parametrize(("job_place", "wanted", "expected"), [
    ("Dayton, OH", "Dayton, OH", True),
    ("Dayton, Ohio", "Dayton, OH", True),
    ("Springfield, OH, USA", "Springfield, OH", True),
    ("Columbus, OH", "Dayton, OH", False),
    ("Powell, Ohio", "OH", True),
    ("Seattle, WA", "OH", False),
])
def test_place_matching(job_place, wanted, expected):
    assert location.place_is_acceptable(job_place, wanted) is expected


def test_same_city_name_in_another_state_is_refused():
    # Clinton exists in dozens of states; only one of them is drivable.
    assert location.place_is_acceptable("Clinton, IA", "Clinton, MS") is False
    assert location.place_is_acceptable("Clinton, MS", "Clinton, MS") is True


def test_any_one_of_several_listed_sites_is_enough():
    ok, why = location.is_commutable("Amsterdam; Dayton, OH", "", NEAR)
    assert ok
    assert "Dayton" in why


def test_empty_location_falls_back_to_the_description():
    ok, why = location.is_commutable(
        "", "This role is based at our Springfield facility.", NEAR)
    assert ok
    assert "description" in why


def test_unstated_location_with_nothing_in_the_body_is_refused():
    ok, why = location.is_commutable("", "A great opportunity to join us.", NEAR)
    assert ok is False
    assert "no location stated" in why


def test_travel_role_based_in_the_area_passes_only_when_travel_is_accepted():
    text = "Company is based in Dayton. Expect travel to project sites."
    assert location.is_commutable("Various", text, NEAR, travel_ok=True)[0] is True
    assert location.is_commutable("Various", text, NEAR, travel_ok=False)[0] is False


def test_no_configured_locations_means_no_filtering():
    ok, why = location.is_commutable("Chicago, IL", "", [])
    assert ok is True
    assert why == ""


def test_remote_postings_skip_the_commute_check_entirely():
    cfg = config.defaults()
    cfg["search"]["locations"] = NEAR
    remote_job = SimpleNamespace(
        title="Support Analyst", location="Remote - United States",
        description="This is a fully remote position. " * 6)
    assert screen.screen_job(remote_job, cfg)["qualified"] == 1


def test_onsite_posting_outside_the_area_is_dropped_with_a_reason():
    cfg = config.defaults()
    cfg["search"]["locations"] = NEAR
    far_job = SimpleNamespace(
        title="Carpenter", location="Phoenix, AZ",
        description="Framing and finish work on commercial sites. " * 6)
    result = screen.screen_job(far_job, cfg)
    assert result["qualified"] == 0
    assert "outside search.locations" in result["screen_reasons"]


def test_onsite_posting_in_the_area_survives():
    cfg = config.defaults()
    cfg["search"]["locations"] = NEAR
    near_job = SimpleNamespace(
        title="Carpenter", location="Springfield, OH",
        description="Framing and finish work on commercial sites. " * 6)
    assert screen.screen_job(near_job, cfg)["qualified"] == 1


# ---- the District of Columbia, and towns named after other states -----------
#
# normalize() used to check full state NAMES first, in dictionary order, and
# stop at the first hit. "Washington, DC" contains "washington", so the
# District resolved to Washington state - which broke the filter in both
# directions at once.

def test_the_district_is_not_washington_state():
    """A District posting satisfying a Washington-state search is a job 2,300
    miles away - the exact false positive this module's docstring says it
    exists to prevent."""
    assert location.normalize("Washington, DC")[1] == "dc"
    assert not location.place_is_acceptable("Washington, DC", "WA")
    assert not location.place_is_acceptable("Washington, DC", "Seattle, WA")


def test_somebody_in_the_district_can_search_for_it():
    """The worse half. A DC posting did not satisfy a "DC" search either, so
    a person in Washington DC filtering on their own city saw none of it -
    and this app ships a USAJOBS collector aimed at the densest federal job
    market in the country."""
    assert location.place_is_acceptable("Washington, DC", "DC")
    assert location.place_is_acceptable("Washington, D.C.", "DC")


def test_washington_state_still_works():
    """The positive control. A rule that simply stopped recognising
    'washington' would satisfy both tests above and break a real state."""
    assert location.normalize("Spokane, Washington")[1] == "wa"
    assert location.place_is_acceptable("Spokane, Washington", "WA")
    assert location.place_is_acceptable("Seattle, WA", "WA")
    assert not location.place_is_acceptable("Seattle, WA", "DC")


@pytest.mark.parametrize(("place", "state"), [
    ("Oregon, OH", "oh"),
    ("Indiana, PA", "pa"),
    ("Nevada, MO", "mo"),
    ("Delaware, OH", "oh"),
    ("Kansas City, MO", "mo"),
])
def test_a_town_named_after_a_state_is_in_the_state_it_is_actually_in(place, state):
    """The same reversal as the District, and there are a lot of them. Every
    one of these read as the state it is named after rather than the one it
    sits in, so its postings answered the wrong search."""
    assert location.normalize(place)[1] == state


def test_a_two_letter_word_early_in_a_name_is_not_the_state():
    """Why the LAST abbreviation wins rather than the first: "La Crosse, WI"
    opens with "la", which is Louisiana."""
    assert location.normalize("La Crosse, WI")[1] == "wi"
    assert location.place_is_acceptable("La Crosse, WI", "WI")
    assert not location.place_is_acceptable("La Crosse, WI", "LA")
