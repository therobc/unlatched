"""Commute filtering: only asked about postings that are not remote.

A carpenter cannot take a job three states away, and before this existed
every posting in the country qualified for him. The rules that matter are
that a bare city name is not enough (city names repeat), and that an
employer based nearby who sends crews out should still be visible to
someone willing to travel.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unlatched import config, location, screen

KNOX = ["Knoxville, TN", "Maryville, TN", "Loudon, TN", "Clinton, TN", "Powell, TN"]


@pytest.mark.parametrize(("job_place", "wanted", "expected"), [
    ("Knoxville, TN", "Knoxville, TN", True),
    ("Knoxville, Tennessee", "Knoxville, TN", True),
    ("Maryville, TN, USA", "Maryville, TN", True),
    ("Nashville, TN", "Knoxville, TN", False),
    ("Powell, Tennessee", "TN", True),
    ("Seattle, WA", "TN", False),
])
def test_place_matching(job_place, wanted, expected):
    assert location.place_is_acceptable(job_place, wanted) is expected


def test_same_city_name_in_another_state_is_refused():
    # Clinton exists in dozens of states; only the Tennessee one is drivable.
    assert location.place_is_acceptable("Clinton, NJ", "Clinton, TN") is False
    assert location.place_is_acceptable("Clinton, TN", "Clinton, TN") is True


def test_any_one_of_several_listed_sites_is_enough():
    ok, why = location.is_commutable("Amsterdam; Knoxville, TN", "", KNOX)
    assert ok
    assert "Knoxville" in why


def test_empty_location_falls_back_to_the_description():
    ok, why = location.is_commutable(
        "", "This role is based at our Maryville facility.", KNOX)
    assert ok
    assert "description" in why


def test_unstated_location_with_nothing_in_the_body_is_refused():
    ok, why = location.is_commutable("", "A great opportunity to join us.", KNOX)
    assert ok is False
    assert "no location stated" in why


def test_travel_role_based_in_the_area_passes_only_when_travel_is_accepted():
    text = "Company is based in Knoxville. Expect travel to project sites."
    assert location.is_commutable("Various", text, KNOX, travel_ok=True)[0] is True
    assert location.is_commutable("Various", text, KNOX, travel_ok=False)[0] is False


def test_no_configured_locations_means_no_filtering():
    ok, why = location.is_commutable("Chicago, IL", "", [])
    assert ok is True
    assert why == ""


def test_remote_postings_skip_the_commute_check_entirely():
    cfg = config.defaults()
    cfg["search"]["locations"] = KNOX
    remote_job = SimpleNamespace(
        title="Support Analyst", location="Remote - United States",
        description="This is a fully remote position. " * 6)
    assert screen.screen_job(remote_job, cfg)["qualified"] == 1


def test_onsite_posting_outside_the_area_is_dropped_with_a_reason():
    cfg = config.defaults()
    cfg["search"]["locations"] = KNOX
    far_job = SimpleNamespace(
        title="Carpenter", location="Phoenix, AZ",
        description="Framing and finish work on commercial sites. " * 6)
    result = screen.screen_job(far_job, cfg)
    assert result["qualified"] == 0
    assert "outside search.locations" in result["screen_reasons"]


def test_onsite_posting_in_the_area_survives():
    cfg = config.defaults()
    cfg["search"]["locations"] = KNOX
    near_job = SimpleNamespace(
        title="Carpenter", location="Maryville, TN",
        description="Framing and finish work on commercial sites. " * 6)
    assert screen.screen_job(near_job, cfg)["qualified"] == 1
