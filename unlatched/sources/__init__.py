"""sources/ - One module per ATS, one shared shape for what they return.

Every collector module exposes:

  SOURCE_NAME              the string used as the "source" half of a job key
  collect(ats_ref, *, fetcher=fetch.fetch) -> list[Job]

`ats_ref` is whatever `discover.py` recorded for that company (a board slug
for most providers, "tenant|wd_host|site" for Workday). `fetcher` defaults to
the real network function but every collector accepts an override, which is
what keeps the whole test suite off the network: a test passes a fake
fetcher that returns canned JSON instead of making a request.

The registry at the bottom maps a source name to its collector, so `cli.py`
and `screen.py` never need to know the individual module names.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape as html_unescape
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType


# Board API responses are one JSON document for the whole board, so a big
# employer legitimately returns far more than the default per-page fetch
# cap - a 219-posting board with full descriptions truncated at the default
# 2MB parsed as invalid JSON and collected ZERO, silently. JSON collectors
# pass this cap instead: still bounded (a stalled/hostile endpoint cannot
# run away), but sized for a real board dump. HTML/page collectors
# (schema_org, sitemap) keep the default page-sized cap.
#
# JSON collectors also pass respect_robots=False. robots.txt is a CRAWLER
# directive for page fetching, and the page-fetching side of this package
# (discovery, schema.org extraction, sitemaps) honors it. The board APIs
# here are different: they are documented, public, published by each ATS
# precisely for programmatic access, and some API hosts robots-disallow
# everything simply to keep search engines out (one real board served
# `User-agent: * Disallow: /` while allowing a single crawler by name -
# honoring that as an API client silently collected zero postings from a
# live board). Deliberate API access is not crawling.
JSON_API_MAX_BYTES = 20_000_000


def decode_board_json(text: str) -> Any:
    """json.loads for a board API response, with one guard: a body that
    fails to parse AND sits at the fetch cap was almost certainly truncated
    mid-document, and that must be an ERROR the collect summary shows for
    the company, never a silent zero-postings result. A short malformed
    body (an HTML error page, an empty string) is still just "no data".
    """
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        if len(text) >= JSON_API_MAX_BYTES - 16:
            raise RuntimeError(
                "board response was truncated at the fetch cap "
                f"({JSON_API_MAX_BYTES} bytes) and could not be parsed - "
                "the board is larger than the cap allows") from None
        return None


@dataclass
class Job:
    """One posting, in the shape every collector normalises to before it
    reaches screen.py. `source_id` is whatever that ATS uses as its own
    identifier - it is combined with the source name to make the stable
    `source:id` key a status entry attaches to for good.
    """

    source: str
    source_id: str
    title: str
    location: str = ""
    url: str = ""
    posted: str = ""
    description: str = ""
    employment_type: str = ""
    # Set only by search sources (USAJOBS), where the collector itself
    # discovers the employer per-posting instead of it being known up front
    # from a companies row. Board collectors never set this - their caller
    # already knows the employer before collect() is called.
    employer: str = ""

    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


def html_to_text(raw: str) -> str:
    """HTML -> text, keeping paragraph breaks so downstream sentence-level
    parsing (schedule detection, requirement sections) has something to
    split on. Collapsing every whitespace run to one space would destroy
    that structure.
    """
    if not raw:
        return ""
    t = html_unescape(raw)
    t = re.sub(r"(?i)<\s*br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol|section)\s*>", "\n", t)
    t = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t\xa0]+", " ", t)
    # Inline markup ("Sell <b>robots</b>.") becomes a space where the tag
    # was, leaving "Sell robots ." in text a person actually reads. Closing
    # the gap before punctuation is safe in a way that dropping the space
    # entirely is not - between two WORDS the space is load-bearing.
    t = re.sub(r"\s+([.,;:!?%)])", r"\1", t)
    t = re.sub(r"(\()\s+", r"\1", t)
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return t.strip()


def registry() -> dict[str, ModuleType]:
    """Built lazily so importing this package never imports every submodule
    (and every submodule's stdlib-only network imports) up front.
    """
    from . import (
        ashby,
        bamboohr,
        breezy,
        greenhouse,
        lever,
        nodesk,
        oracle_hcm,
        recruitee,
        remoteok,
        schema_org,
        sitemap,
        smartrecruiters,
        usajobs,
        workable,
        workday,
    )
    return {
        greenhouse.SOURCE_NAME: greenhouse,
        lever.SOURCE_NAME: lever,
        ashby.SOURCE_NAME: ashby,
        smartrecruiters.SOURCE_NAME: smartrecruiters,
        workable.SOURCE_NAME: workable,
        recruitee.SOURCE_NAME: recruitee,
        workday.SOURCE_NAME: workday,
        oracle_hcm.SOURCE_NAME: oracle_hcm,
        bamboohr.SOURCE_NAME: bamboohr,
        breezy.SOURCE_NAME: breezy,
        schema_org.SOURCE_NAME: schema_org,
        sitemap.SOURCE_NAME: sitemap,
        usajobs.SOURCE_NAME: usajobs,
        remoteok.SOURCE_NAME: remoteok,
        nodesk.SOURCE_NAME: nodesk,
    }


def search_sources(reg: dict[str, ModuleType]) -> dict[str, ModuleType]:
    """Split a registry into just its search sources - collectors that take
    the whole config and run once per `collect`, independent of any single
    company (USAJOBS is a national keyword+location search, not a
    per-employer board). `cli.py` runs these after the company loop instead
    of alongside it; this is the one place that knows the distinction, so
    neither `cli.py` nor a future search collector has to re-derive it.
    """
    return {name: mod for name, mod in reg.items() if getattr(mod, "IS_SEARCH_SOURCE", False)}
