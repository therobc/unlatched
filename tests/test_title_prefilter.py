"""A detail request is not made for a posting the title has already rejected.

MEASURED, on a real Oracle tenant: 907 stored postings, SEVEN passing the
profile's own search.title_include, zero qualified. The collector was making up
to 500 detail requests - one per posting, about two seconds each - to keep
seven, and that single employer took ten minutes out of every run.

The load-bearing assertion is the count of DETAIL requests, not the count of
returned jobs. A test that only checked the jobs would pass against a version
that still made every request and simply threw the answers away.
"""
from __future__ import annotations

import json
from typing import Any

from unlatched import cli, db
from unlatched.sources import oracle_hcm

HOST = "tenant.example.com"

WANTED = "Application Support Specialist"
UNWANTED = "Senior Petroleum Geologist"


def _listing(count: int) -> dict[str, Any]:
    """The shape the real endpoint returns.

    WRAPPED IN "items", because that is what _list_page unwraps. A fake that
    skipped the envelope made the collector see no postings at all, and the
    "one detail request" assertion then failed at zero - which reads exactly
    like a filter working, rather than a fixture that was never parsed.
    """
    return {"items": [{
        "TotalJobsCount": count,
        "requisitionList": [
            {"Id": str(i), "Title": WANTED if i == 0 else UNWANTED,
             "PrimaryLocation": "Remote", "PostedDate": "2026-08-01"}
            for i in range(count)
        ],
    }]}


def _fetcher(seen: list[str], count: int = 6) -> Any:
    def fetch(url: str, **_kw: Any) -> tuple[int, str, str]:
        seen.append(url)
        if "recruitingCEJobRequisitionDetails" in url:
            return 200, json.dumps({"items": [{
                "ExternalDescriptionStr": "a description nobody asked for",
            }]}), url
        if "recruitingCEJobRequisitions" in url:
            first = "offset=0" in url or "offset=" not in url
            body = _listing(count) if first else {"items": [{"requisitionList": []}]}
            return 200, json.dumps(body), url
        return 404, "", url
    return fetch


def _details(urls: list[str]) -> list[str]:
    return [u for u in urls if "recruitingCEJobRequisitionDetails" in u]


def test_only_titles_that_could_pass_get_a_detail_request():
    seen: list[str] = []
    jobs = oracle_hcm.collect(HOST, fetcher=_fetcher(seen),
                              title_include=[WANTED])

    # THE POINT: one wanted title, one detail request - not six.
    assert len(_details(seen)) == 1, _details(seen)
    # And every posting still comes back, from the list page.
    assert len(jobs) == 6
    assert sum(1 for j in jobs if j.description) == 1


def test_the_rejected_postings_are_still_returned():
    """Skipping the REQUEST must not skip the POSTING - delisting and
    reconciliation both depend on seeing everything the board still lists."""
    seen: list[str] = []
    jobs = oracle_hcm.collect(HOST, fetcher=_fetcher(seen),
                              title_include=[WANTED])

    titles = [j.title for j in jobs]
    assert titles.count(UNWANTED) == 5
    assert all(j.url for j in jobs)


def test_no_filter_means_every_posting_still_gets_its_detail():
    """The positive control, and what an unset title_include must do.

    Without it, a fix that simply stopped fetching details would pass the first
    test and quietly strip descriptions from every profile.
    """
    seen: list[str] = []
    oracle_hcm.collect(HOST, fetcher=_fetcher(seen))
    assert len(_details(seen)) == 6

    seen.clear()
    oracle_hcm.collect(HOST, fetcher=_fetcher(seen), title_include=[])
    assert len(_details(seen)) == 6


def test_the_filter_agrees_with_the_screen_that_will_run_later():
    """A looser rule here only wastes requests; a stricter one skips the
    description of a posting that goes on to qualify. So it must use the same
    matcher screening uses, including its word-boundary behaviour."""
    from unlatched.screen import title_wants

    for title in ("Application Support Specialist",
                  "Senior Application Support Specialist",
                  "Application Support Specialist II"):
        assert oracle_hcm._title_may_pass(title, [WANTED]) is True  # noqa: SLF001
        assert title_wants(WANTED, title) is True

    assert oracle_hcm._title_may_pass(UNWANTED, [WANTED]) is False  # noqa: SLF001


def test_the_collector_declares_that_it_wants_the_filter():
    # The caller only passes it to collectors that opt in, because they do not
    # share a signature.
    assert oracle_hcm.WANTS_TITLE_INCLUDE is True


def test_a_collect_passes_the_profiles_filter_through(tmp_path, monkeypatch):
    """End to end through cmd_collect, because the wiring is where this would
    silently stop working - the collector would keep its parameter and simply
    never be given one."""
    home = tmp_path / "home"
    con = db.connect(home)
    db.upsert_company(con, "Tenant Co", ats="oracle_hcm", ats_ref=HOST,
                      probe_status="probed")
    con.close()

    # Through the config module, which is what writes the file - db.connect
    # does not, so reading config.json straight after connecting finds nothing.
    from unlatched import config
    cfg = config.load(home)
    cfg.setdefault("search", {})["title_include"] = [WANTED]
    config.save(cfg, home)

    seen: list[str] = []
    monkeypatch.setattr(cli.fetch_mod, "fetch", _fetcher(seen))

    assert cli.main(["--home", str(home), "collect"]) == 0
    assert len(_details(seen)) == 1, _details(seen)

def test_a_later_run_does_not_erase_a_description_it_did_not_fetch(tmp_path, monkeypatch):
    """The regression that measuring caught.

    Run once with no title filter, so every posting gets its description. Run
    again WITH the filter, so most are skipped. The skipped rows must keep the
    text the first run collected - the second run has nothing to say about
    those columns, and nothing is not an answer.
    """
    from unlatched import config

    home = tmp_path / "home"
    con = db.connect(home)
    db.upsert_company(con, "Tenant Co", ats="oracle_hcm", ats_ref=HOST,
                      probe_status="probed")
    con.close()

    seen: list[str] = []
    monkeypatch.setattr(cli.fetch_mod, "fetch", _fetcher(seen))

    # First run: no filter, so every posting is fetched in full.
    assert cli.main(["--home", str(home), "collect"]) == 0
    con = db.connect(home)
    before = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE description IS NOT NULL AND description != ''"
    ).fetchone()[0]
    con.close()
    assert before == 6, f"fixture did not populate descriptions: {before}"

    # Second run: the filter now skips five of the six detail requests.
    cfg = config.load(home)
    cfg.setdefault("search", {})["title_include"] = [WANTED]
    config.save(cfg, home)
    seen.clear()
    assert cli.main(["--home", str(home), "collect"]) == 0
    assert len(_details(seen)) == 1, _details(seen)

    con = db.connect(home)
    after = con.execute(
        "SELECT COUNT(*) FROM jobs WHERE description IS NOT NULL AND description != ''"
    ).fetchone()[0]
    con.close()
    assert after == 6, (
        f"{before - after} stored description(s) were erased by a run that "
        f"simply did not fetch them"
    )


# ---- the same decision, in the other collector that makes it ---------------
#
# sitemap.py pre-filters by the title it can read out of the URL slug. It used
# a plain substring test while oracle_hcm used screen.title_wants, and
# substring is STRICTER: it needs the words of a term contiguous and spelled
# exactly. So "HR Specialist" stopped matching "HR Operations Specialist".
#
# It is the worse of the two places to get this wrong. oracle_hcm skips only
# the DETAIL request and still returns the posting; sitemap drops the URL, so
# the posting is never fetched, never returned, and nobody learns it existed.

SITE = "portal.example.com"
MATCHES_BY_WORD = f"https://{SITE}/careers/hr-operations-specialist"
MATCHES_NOTHING = f"https://{SITE}/careers/senior-petroleum-geologist"


def _sitemap_fetcher(asked: list[str]):
    """Records every URL requested, and serves a two-posting sitemap."""
    def fetcher(url: str, **_: Any) -> tuple[int, str, str]:
        asked.append(url)
        if url.endswith("/robots.txt"):
            return 200, f"Sitemap: https://{SITE}/sitemap.xml\n", url
        if url.endswith("/sitemap.xml"):
            locs = "".join(f"<loc>{u}</loc>"
                           for u in (MATCHES_BY_WORD, MATCHES_NOTHING))
            return 200, f"<urlset>{locs}</urlset>", url
        # A posting page with no JSON-LD: this test is about WHICH urls get
        # fetched, not about what parses out of them.
        return 200, "<html></html>", url
    return fetcher


def test_sitemap_prefilter_keeps_a_title_whose_words_are_not_adjacent():
    """The regression. "HR Specialist" wants "HR Operations Specialist".

    The assertion is on what was FETCHED, because that is what the pre-filter
    decides. Asserting on returned jobs would pass against a version that
    fetched everything and discarded the answers - and against this one, which
    returns nothing either way because the fixture pages carry no JSON-LD.
    """
    from unlatched.sources import sitemap

    asked: list[str] = []
    sitemap.collect(SITE, fetcher=_sitemap_fetcher(asked),
                    title_include=["HR Specialist"])

    assert MATCHES_BY_WORD in asked, (
        "sitemap dropped a posting whose title contains every word of the "
        "search term. This is the substring bug: the words are not adjacent.")
    assert MATCHES_NOTHING not in asked, (
        "sitemap fetched a posting matching none of the search term - the "
        "pre-filter is not running at all")


def test_the_two_collectors_answer_the_same_question_the_same_way():
    """One rule, one place. They disagreed for as long as there were two.

    Compared through screen.title_may_pass, which is now what both of them
    call - so this fails if either grows its own copy again.
    """
    from unlatched.screen import title_may_pass

    for title, wanted, expected in (
            ("HR Operations Specialist", "HR Specialist", True),
            ("Senior Application Support Specialist", WANTED, True),
            (UNWANTED, WANTED, False)):
        assert title_may_pass(title, [wanted]) is expected
        assert oracle_hcm._title_may_pass(title, [wanted]) is expected  # noqa: SLF001

    # No filter means everything passes, on both paths.
    assert title_may_pass(UNWANTED, None) is True
    assert title_may_pass(UNWANTED, []) is True


# ---- the same decision, a third time: the SCORE -----------------------------
#
# screen_job gates the title with title_wants and then awarded its +20 bonus
# with a plain substring test. So a posting could pass the gate BECAUSE the
# matcher is generous and then be denied the bonus FOR being the kind of match
# that needed it: "IT HelpDesk Analyst" against "help desk" qualified at 60
# where the exact phrase scored 80. On a score-sorted list that is the
# difference between the first screen and the third.


class _Posting:
    """Anything with the attributes screen_job reads."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.location = "Remote"
        self.employment_type = ""
        # Over MIN_JD_CHARS_TO_JUDGE, so nothing else marks the row alt and
        # the only thing varying between these cases is the title.
        self.description = "Provides day to day support to staff. " * 12


def _score(title: str, wanted: list[str]) -> float:
    from unlatched.screen import screen_job

    cfg = {"search": {"title_include": wanted, "us_only": True},
           "profile": {}, "skills": []}
    return float(screen_job(_Posting(title), cfg)["score"])


def test_a_title_the_gate_accepts_is_scored_as_a_title_match():
    """The property, not the implementation: if the gate takes it, the score
    agrees. Written this way so a third matcher appearing here fails the test
    whatever that matcher is."""
    from unlatched.screen import title_may_pass

    wanted = ["HR Specialist", "help desk"]
    exact = _score("HR Specialist", wanted)

    for title in ("HR Operations Specialist",   # words present, not adjacent
                  "HR Specialists",             # plural
                  "IT HelpDesk Analyst"):       # compound spelling
        assert title_may_pass(title, wanted) is True, title
        assert _score(title, wanted) == exact, (
            f"{title!r} passes the title gate but scores {_score(title, wanted)} "
            f"against {exact} for an exact phrase - the bonus is using a "
            f"different matcher than the gate")


def test_a_title_the_gate_rejects_gets_no_title_bonus():
    """The positive control. Without it, a bonus awarded unconditionally would
    satisfy the test above perfectly."""
    from unlatched.screen import title_may_pass

    wanted = ["HR Specialist"]
    assert title_may_pass(UNWANTED, wanted) is False
    assert _score(UNWANTED, wanted) < _score("HR Specialist", wanted)
