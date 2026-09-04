"""An employer that changes ATS goes quiet, and nothing used to say why.

`collect` keeps reading the board reference it was given. When that reference
stops returning postings the employer simply disappears from the results, and
nothing in the app tells the difference between "not hiring" and "moved to
Greenhouse two months ago". The starter pack sharpens it: fifty employers
measured on one day, ageing together.

These cover the four outcomes and, more importantly, the two rules that decide
whether anything is WRITTEN - because the board reference this command
rewrites is what every future collect depends on.
"""
from __future__ import annotations

from typing import Any

import pytest

from unlatched import db, discover, rediscover

# Fingerprint-bearing pages, as detect_ats expects to find them. Enough real
# markup for the length floor discover.resolve applies before trusting a page.
PAD = "<p>About us. We are hiring.</p>" * 30

WORKDAY = (
    f"<html><body>{PAD}"
    '<a href="https://acme.wd5.myworkdayjobs.com/en-US/External">Careers</a>'
    "</body></html>"
)

GREENHOUSE = (
    f"<html><body>{PAD}"
    '<a href="https://boards.greenhouse.io/acmecorp">Careers</a>'
    "</body></html>"
)

NOTHING = f"<html><body>{PAD}<p>No jobs listed today.</p></body></html>"


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path)
    yield c
    c.close()


def _resolver(monkeypatch, mapping: dict[str, tuple[str, str]]):
    """Stub discover.resolve: {company: (ats_provider, ats_ref)}.

    STUBBED AT resolve() RATHER THAN AT THE FETCHER. Driving real HTML through
    the whole discovery walk would be testing discover.py, which has its own
    tests; what these need to pin is what rediscover DOES with an answer.
    """
    def fake(company: str, *, fetcher: Any = None) -> dict[str, Any]:
        provider, ref = mapping.get(company, ("", ""))
        parts = ref.split("|") if ref else []
        return {
            "company": company, "domain": "example.com",
            "careers_url": f"https://{company.lower()}.example.com/careers",
            "ats": ([{"provider": provider, "parts": parts}] if provider else []),
            "portals": [], "note": "" if provider else "no domain resolved",
        }
    monkeypatch.setattr(discover, "resolve", fake)


def _seed(con, name: str, ats: str, ref: str) -> None:
    db.upsert_company(con, name, ats=ats, ats_ref=ref,
                      careers_url=f"https://{name.lower()}.example.com/",
                      probe_status="yielding" if ats else "probed")


# ---- the four outcomes ----------------------------------------------------

def test_an_employer_on_the_same_board_is_unchanged(con, monkeypatch):
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _resolver(monkeypatch, {"Acme": ("workday", "acme|wd5|External")})

    found = rediscover.plan(con, fetcher=None)
    assert [f.outcome for f in found] == [rediscover.UNCHANGED]


def test_an_employer_that_switched_systems_reads_as_moved(con, monkeypatch):
    """The case the whole command exists for."""
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _resolver(monkeypatch, {"Acme": ("greenhouse", "acmecorp")})

    found = rediscover.plan(con, fetcher=None)
    assert [f.outcome for f in found] == [rediscover.MOVED]
    assert found[0].was_ats == "workday"
    assert found[0].now_ats == "greenhouse"
    assert found[0].now_ref == "acmecorp"


def test_the_same_system_at_a_new_reference_is_also_a_move(con, monkeypatch):
    """A tenant rename breaks collection exactly as hard as a system change.

    The provider is identical, so a check that compared only the provider
    would call this unchanged and the employer would stay quiet.
    """
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _resolver(monkeypatch, {"Acme": ("workday", "acmecorp|wd1|Careers")})

    found = rediscover.plan(con, fetcher=None)
    assert [f.outcome for f in found] == [rediscover.MOVED]


def test_an_employer_whose_board_vanished_reads_as_unreadable(con, monkeypatch):
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _resolver(monkeypatch, {"Acme": ("", "")})

    found = rediscover.plan(con, fetcher=None)
    assert [f.outcome for f in found] == [rediscover.UNREADABLE]


def test_an_employer_that_gained_a_board_reads_as_now_readable(con, monkeypatch):
    _seed(con, "Acme", "", "")
    _resolver(monkeypatch, {"Acme": ("ashby", "acme")})

    found = rediscover.plan(con, fetcher=None)
    assert [f.outcome for f in found] == [rediscover.NOW_READABLE]


def test_an_employer_that_never_had_a_board_and_still_does_not_is_not_an_error(
        con, monkeypatch):
    """Not every employer fingerprints, and that is an ordinary state - it is
    what the schema.org and sitemap collectors are for."""
    _seed(con, "Acme", "", "")
    _resolver(monkeypatch, {"Acme": ("", "")})

    found = rediscover.plan(con, fetcher=None)
    assert [f.outcome for f in found] == [rediscover.UNCHANGED]


# ---- what gets written, and what deliberately does not --------------------

def test_planning_never_writes(con, monkeypatch):
    """The dry run has to be safe to run out of curiosity."""
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _resolver(monkeypatch, {"Acme": ("greenhouse", "acmecorp")})

    rediscover.plan(con, fetcher=None)

    row = db.get_company(con, "Acme")
    assert row["ats"] == "workday", "plan() rewrote the stored reference"
    assert row["ats_ref"] == "acme|wd5|External"


def test_applying_writes_the_move(con, monkeypatch):
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _resolver(monkeypatch, {"Acme": ("greenhouse", "acmecorp")})

    found = rediscover.plan(con, fetcher=None)
    result = rediscover.apply(con, found)

    assert result["written"] == 1
    row = db.get_company(con, "Acme")
    assert row["ats"] == "greenhouse"
    assert row["ats_ref"] == "acmecorp"


def test_a_failed_probe_never_blanks_a_working_reference(con, monkeypatch):
    """THE RULE WORTH TESTING TWICE.

    One probe finding nothing is not evidence the board is gone - a site
    mid-migration, a redirect, a bad afternoon all look the same. Blanking the
    reference on that would take the employer out of every future collect, and
    the person would have to notice the silence and work out why.
    """
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _resolver(monkeypatch, {"Acme": ("", "")})

    found = rediscover.plan(con, fetcher=None)
    assert found[0].outcome == rediscover.UNREADABLE
    result = rediscover.apply(con, found)

    assert result["written"] == 0
    row = db.get_company(con, "Acme")
    assert row["ats"] == "workday", "an unreadable probe erased a working board"
    assert row["ats_ref"] == "acme|wd5|External"


# ---- scoping and reporting ------------------------------------------------

def test_one_company_can_be_checked_on_its_own(con, monkeypatch):
    """Fifty discoveries is a long errand when the question is about one."""
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _seed(con, "Beta", "lever", "beta")
    _resolver(monkeypatch, {"Acme": ("greenhouse", "acmecorp"),
                            "Beta": ("lever", "beta")})

    found = rediscover.plan(con, fetcher=None, only="acme")
    assert [f.company for f in found] == ["Acme"], "only= swept the wrong set"


def test_the_tally_names_every_outcome_even_at_zero(con, monkeypatch):
    """A missing key and a zero read the same to somebody skimming a summary,
    and the summary line is built from this."""
    _seed(con, "Acme", "workday", "acme|wd5|External")
    _resolver(monkeypatch, {"Acme": ("workday", "acme|wd5|External")})

    counts = rediscover.tally(rediscover.plan(con, fetcher=None))
    assert set(counts) == {rediscover.UNCHANGED, rediscover.MOVED,
                           rediscover.UNREADABLE, rediscover.NOW_READABLE}
    assert counts[rediscover.UNCHANGED] == 1
    assert counts[rediscover.MOVED] == 0


# ---- the shared rule ------------------------------------------------------

def test_both_commands_read_a_resolve_result_the_same_way():
    """discover and rediscover ask the same question of the same structure.

    This lived inline in cmd_discover and would have been written a second
    time here. Two copies of one rule is what put sitemap.py and oracle_hcm.py
    into disagreement about title matching, so it is shared and pinned.
    """
    compound = {"ats": [{"provider": "workday",
                         "parts": ["acme", "wd5", "External"]}]}
    assert discover.ats_of(compound) == ("workday", "acme|wd5|External")

    simple = {"ats": [{"provider": "greenhouse", "parts": ["acmecorp"]}]}
    assert discover.ats_of(simple) == ("greenhouse", "acmecorp")

    assert discover.ats_of({"ats": []}) == ("", "")
    assert discover.ats_of({}) == ("", "")


def test_the_fixtures_carry_a_real_fingerprint():
    """A positive control on the fixture HTML itself.

    Every outcome test above stubs resolve(), so none of them would notice if
    the markup here stopped fingerprinting. This is the one place that holds
    the fixtures to detect_ats, so they cannot rot into decoration.
    """
    assert discover.detect_ats(WORKDAY), "the workday fixture fingerprints as nothing"
    assert discover.detect_ats(GREENHOUSE), "the greenhouse fixture fingerprints as nothing"
    assert not discover.detect_ats(NOTHING), "the empty fixture fingerprints as something"
