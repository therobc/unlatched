"""Which hand-added links are worth a request, and which are not.

"Is this still open" only earns a request when the answer changes what the
person does - which is only true for a job they have not decided about yet.

The old behaviour was the opposite on both counts: it re-read postings it had
ALREADY recorded as closed, and it put applied-to jobs FIRST. Every one of those
requests went to a site this app reads only with a person present, so the waste
was not merely bandwidth.
"""
from __future__ import annotations

from datetime import UTC, datetime

from unlatched import db, manual, status

STALE = "2026-08-01T00:00:00+00:00"
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def add(con, key, *, last_seen=STALE, delisted=None, source=manual.SOURCE_NAME):
    cid = db.upsert_company(con, "Acme")
    db.upsert_job(con, key, {
        "company_id": cid, "title": "Analyst", "source": source,
        "url": f"https://www.example.com/jobs/view/{key.split(':')[-1]}",
        "last_seen": last_seen, "delisted_at": delisted,
        "qualified": 1, "verdict": "keep",
    })


def due_keys(con):
    return [r["key"] for r in manual.due_rows(con, NOW)]


def test_the_default_scope_is_hand_added_rows_and_nothing_else():
    """A profile with no collector asking for anything gets exactly the old
    behaviour. The list is now built per profile, so the floor is asserted
    rather than assumed."""
    assert manual.ALWAYS_RECHECKABLE == ("manual",)
    assert manual.recheckable_sources({}) == ("manual",)


def test_an_untouched_open_posting_is_checked(con):
    add(con, "manual:1")
    assert due_keys(con) == ["manual:1"]


def test_a_posting_already_marked_closed_is_never_re_read(con):
    """It was recorded as gone. Asking again every day forever spends requests
    to learn what is already known - and a 200 from a sign-in wall or a rebuilt
    page could take it back OUT of delisted and present a dead posting as live."""
    add(con, "manual:1", delisted="2026-08-05T10:00:00")
    assert due_keys(con) == []


def test_nothing_with_a_status_is_re_read(con):
    """If they have touched it, the posting's liveness no longer drives an
    action. Every status below follows from that one rule rather than from a
    list somebody wrote out."""
    for index, state in enumerate(
            ["applied", "pass", "denied", "interviewed", "offer", "hired", "closed"]):
        key = f"manual:{index}"
        add(con, key)
        status.set_status(con, key, state)
    assert due_keys(con) == []


def test_clearing_a_status_puts_a_job_back_in_scope(con):
    """Untouched is a live property, not a permanent verdict - somebody who
    marks a job by mistake and clears it should get the check back."""
    add(con, "manual:1")
    status.set_status(con, "manual:1", "pass")
    assert due_keys(con) == []
    status.clear_status(con, "manual:1")
    assert due_keys(con) == ["manual:1"]


def test_imported_rows_are_never_re_read_by_this_button(con):
    """THE POINT OF THE CHANGE (decided 2026-08-12).

    Imported rows were in scope on the reasoning that no board was watching
    them. That stopped being true: the collector that sends them now detects
    closures and pushes them in, so re-reading them is a second automated
    reader for information this app is already handed.

    Measured before the change on a real profile: all 334 rows this button would
    have fetched were imported MyBoard URLs, and not one was hand-added.
    """
    add(con, "manual:1")
    add(con, "imported:1", source="imported")
    assert due_keys(con) == ["manual:1"]


def test_an_untouched_imported_row_is_still_out_of_scope(con):
    """A NEGATIVE CONTROL for the test above.

    Without it, a version that excluded imported rows for some incidental
    reason - already decided, already closed - would pass while the scope was
    still wrong. This row trips none of the other exclusions: open, untouched,
    stale enough to be due. It is excluded on source alone.
    """
    add(con, "imported:untouched", source="imported")
    assert due_keys(con) == []


def test_collected_rows_are_never_in_this_population(con):
    """A board IS watching those, and the collect marks them delisted. Reading
    them here would be a second reader for no new information."""
    add(con, "greenhouse:1", source="greenhouse")
    add(con, "usajobs:1", source="usajobs")
    assert due_keys(con) == []


def test_a_recently_read_posting_still_waits_its_turn(con):
    """The once-a-day rule is unchanged: pressing the button five times in an
    afternoon must not read the same page five times."""
    add(con, "manual:1", last_seen="2026-08-09T11:00:00+00:00")
    assert due_keys(con) == []


def test_the_count_beside_the_button_matches_what_will_be_read(con):
    """A number that promises more checks than happen is one a person learns
    to disbelieve."""
    add(con, "manual:untouched")
    add(con, "manual:applied")
    status.set_status(con, "manual:applied", "applied")
    add(con, "manual:closed", delisted="2026-08-05T10:00:00")

    state = manual.recheck_status(con, NOW)
    assert state["total"] == 1
    assert state["due"] == 1
    assert len(due_keys(con)) == state["due"]


def test_recheck_marks_a_gone_posting_and_reports_it(con, cfg):
    """The one thing this path is for still works."""
    add(con, "manual:1")
    reading_on = {"fetch": {"read_added_links": True}}

    def gone(_url, **_kwargs):
        return (404, "", _url)

    result = manual.recheck(con, reading_on, fetcher=gone, now=NOW)
    assert result["gone"] == ["manual:1"]
    assert db.get_job(con, "manual:1")["delisted_at"]
    # And having been marked, it is out of scope from now on.
    assert due_keys(con) == []


def test_recheck_is_a_liveness_check_and_never_re_reads_the_details(con, cfg):
    """The question: does a row that already HAS its description get it
    fetched again?

    No. recheck asks one thing - is this posting still there - and writes only
    delisted_at and last_seen. It never re-parses the page or touches the
    title, employer or description, so an imported row that arrived complete is
    never re-scraped for content it already has.
    """
    add(con, "imported:1", source="imported")
    con.execute(
        "UPDATE jobs SET title = ?, description = ? WHERE key = ?",
        ("Technology Operations Support Analyst",
         "The description the other app already gathered.", "imported:1"))
    con.commit()

    served = (
        "<html><h1>A COMPLETELY DIFFERENT TITLE</h1>"
        "<div class='show-more-less-html__markup'>Different body.</div></html>"
    )

    def still_listed(_url, **_kwargs):
        return (200, served, _url)

    manual.recheck(con, {"fetch": {"read_added_links": True}},
                   fetcher=still_listed, now=NOW)

    row = db.get_job(con, "imported:1")
    assert row["title"] == "Technology Operations Support Analyst"
    assert row["description"] == "The description the other app already gathered."


def test_a_run_is_capped_and_the_next_press_takes_the_next_batch(con):
    """The cap is per PRESS, not per day. Somebody lining up a lot of
    applications works through a backlog by pressing again; the once-per-20-
    hours rule is what stops the same job being read twice."""
    for index in range(manual.RECHECK_MAX_PER_RUN + 10):
        add(con, f"manual:{index}", last_seen=f"2026-08-0{1 + index % 5}T00:00:00+00:00")

    first = due_keys(con)
    assert len(first) == manual.RECHECK_MAX_PER_RUN

    # Reading them stamps last_seen, which drops them out of "due".
    con.executemany("UPDATE jobs SET last_seen = ? WHERE key = ?",
                    [("2026-08-09T11:59:00+00:00", key) for key in first])
    con.commit()

    second = due_keys(con)
    assert second, "the next press should pick up the rest"
    assert not set(second) & set(first), "and none of the ones just read"


def test_the_batch_size_relies_on_pacing_that_is_actually_engaged(con, cfg):
    """The cap was raised because fetch.py now paces and backs off. If that
    stopped applying to this path, 50 requests would go out back to back - so
    the dependency is asserted rather than assumed.
    """
    from unlatched import fetch

    add(con, "manual:1")
    seen = {}

    def record(url, **kwargs):
        seen.update(kwargs)
        return (200, "<html>still here</html>", url)

    manual.recheck(con, {"fetch": {"read_added_links": True}},
                   fetcher=record, now=NOW)

    # recheck must not disable the per-host delay by passing its own value.
    assert "per_host_delay_s" not in seen, \
        "recheck must inherit fetch.py's pacing, not override it"
    assert fetch.DEFAULT_PER_HOST_DELAY_S > 0
    assert fetch.THROTTLE_LIMIT > 0, "and the throttle stop must exist"


def test_recheck_reads_nothing_when_the_setting_is_off(con, cfg):
    """cfg is the shipped default, which has added-link reading OFF."""
    add(con, "manual:1")

    def explode(*_args, **_kwargs):
        raise AssertionError("nothing should be requested")

    assert manual.recheck(con, cfg, fetcher=explode, now=NOW)["checked"] == 0


def test_the_count_reports_links_read_not_links_found_closed(con):
    """The healthy outcome must not report as nothing.

    Every link read, every one still open - which is what most runs look like.
    This reported "checked 0 added links" because the count was len(gone), so
    the run that did the most work claimed to have done none.
    """
    for n in (1, 2, 3):
        add(con, f"manual:{n}")

    def live(*_args, **_kwargs):
        return 200, "<html><body>Still hiring, apply today.</body></html>", ""

    result = manual.recheck(con, {"fetch": {"read_added_links": True}},
                            fetcher=live, now=NOW)

    assert result["checked"] == 3, (
        f"three links were read and none had closed; the count says "
        f"{result['checked']}")
    assert result["gone"] == []
    assert result["unreadable"] == []


def test_a_closure_is_still_counted_among_those_read(con):
    """Positive control. Counting rows instead of closures must not stop the
    closures being detected - the count and the list are different answers to
    different questions, and both have to stay right."""
    for n in (1, 2):
        add(con, f"manual:{n}")

    def one_gone(url, *_args, **_kwargs):
        if url.endswith("/2"):
            return 404, "", url
        return 200, "<html><body>Still hiring.</body></html>", url

    result = manual.recheck(con, {"fetch": {"read_added_links": True}},
                            fetcher=one_gone, now=NOW)

    assert result["checked"] == 2, "both links were read"
    assert result["gone"] == ["manual:2"], "the closed one is still detected"
