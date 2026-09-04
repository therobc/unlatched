"""The apply destination: the key that joins one job across two boards.

A posting on one board is often a shopfront for an application hosted on
another - a LinkedIn listing routing to apply.workable.com - so the same job
can arrive twice and only this field says so exactly.

It has to survive two things that both defeat naive comparison: interstitial
redirects that hide the destination behind the referring board's own host, and
tracking parameters that differ every time the same link is referred.
"""
from __future__ import annotations

from unlatched.links import normalise_apply_url, unwrap_redirect

WORKABLE = "https://apply.workable.com/northwind/j/ABC123/"


def test_the_same_destination_reached_two_ways_is_one_key():
    """The whole point: a LinkedIn row and a Workable row must collide."""
    wrapped = (
        "https://www.linkedin.com/safety/go/?url="
        "https%3A%2F%2Fapply%2Eworkable%2Ecom%2Fnorthwind%2Fj%2FABC123%2F"
        "&trk=public_jobs_apply-link-offsite"
    )
    assert normalise_apply_url(wrapped) == normalise_apply_url(WORKABLE)


def test_a_wrapped_link_is_not_judged_by_the_wrapper_s_host():
    """Reported from the backfill: a naive off-site test rejected every
    LinkedIn row, because the href itself is on linkedin.com."""
    wrapped = (
        "https://www.linkedin.com/safety/go/?url="
        "https%3A%2F%2Fapply%2Eworkable%2Ecom%2Fnorthwind%2Fj%2FABC123%2F"
    )
    assert "linkedin.com" not in normalise_apply_url(wrapped)
    assert "workable.com" in normalise_apply_url(wrapped)


def test_percent_encoded_dots_survive():
    """LinkedIn encodes the dots too (%2E), so the target is unusable until it
    is unquoted - and a host read before unquoting is nonsense."""
    wrapped = "https://linkedin.com/safety/go/?url=https%3A%2F%2Fapply%2Eworkable%2Ecom%2Fx"
    assert unwrap_redirect(wrapped) == "https://apply.workable.com/x"


def test_tracking_parameters_are_dropped_but_the_job_id_is_not():
    """The decision this function turns on. Query strings carry BOTH the thing
    that identifies the job and the thing that identifies the referrer."""
    with_tracking = "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=abc&utm_medium=email"
    clean = "https://boards.greenhouse.io/acme/jobs/4012345"
    assert normalise_apply_url(with_tracking) == normalise_apply_url(clean)


def test_two_jobs_at_one_employer_never_collapse_into_one():
    """The failure that would matter. Dropping the query wholesale would make
    every opening at an employer that keys on it look like the same job -
    a false merge HIDES a job somebody wanted, and they never learn it existed.
    """
    first = normalise_apply_url("https://jobs.example.com/apply?jobId=88213")
    second = normalise_apply_url("https://jobs.example.com/apply?jobId=99117")
    assert first != second


def test_parameter_order_and_case_do_not_make_one_job_look_like_two():
    a = normalise_apply_url("https://Jobs.Example.com/apply?b=2&a=1")
    b = normalise_apply_url("https://jobs.example.com/apply/?a=1&b=2")
    assert a == b


def test_an_absent_destination_never_matches_another_absent_one():
    """Easy Apply is a real answer, not a gap: the application stays on the
    board and there is no ATS row it could collide with. Two of them must not
    be read as the same job."""
    assert normalise_apply_url("") == ""
    assert normalise_apply_url(None) == ""
    # And nothing that is not a usable link becomes a key either.
    assert normalise_apply_url("file:///C:/Windows/System32/calc.exe") == ""
    assert normalise_apply_url("javascript:void(0)") == ""


def test_an_ordinary_link_is_left_alone():
    """Only NAMED wrappers are unwrapped. A genuine application link carrying a
    url parameter of its own keeps it - unwrapping that would throw away the
    real destination."""
    ordinary = "https://apply.example.com/start?url=https%3A%2F%2Felsewhere.example%2Fx"
    assert "apply.example.com" in normalise_apply_url(ordinary)


def test_a_redirect_chain_terminates():
    """A wrapper around a wrapper is real. A chain that never ends is a loop,
    and this must not spin on one."""
    looping = "https://linkedin.com/safety/go/?url=https%3A%2F%2Flinkedin%2Ecom%2Fsafety%2Fgo"
    assert unwrap_redirect(looping)  # returns something rather than hanging
