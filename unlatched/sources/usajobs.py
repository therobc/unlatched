"""usajobs.py - USAJOBS Search API (data.usajobs.gov), the federal government's
own documented, free, keyed public API.

Architecturally different from every other module in this package: the rest
are per-employer BOARDS (`collect(ats_ref)` where `ats_ref` names one
company's ATS instance). USAJOBS is a national SEARCH - keywords and
locations in, postings from any of ~450 hiring agencies out. `IS_SEARCH_SOURCE
= True` marks that, and `collect(cfg)` takes the whole config instead of an
`ats_ref`; `sources.search_sources()` is what tells `cli.py` which collectors
need to be called this way.

Auth is three headers, not a query param - `Authorization-Key`, `Host`, and a
`User-Agent` set to the caller's OWN registered email (not this package's UA
string, which is why `fetch()` grew a `headers` override just for this
module). A key is free from developer.usajobs.gov; `config.py`'s
`credentials.usajobs` block is where it lives.
"""
from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING, Any

from unlatched.fetch import fetch as default_fetch
from unlatched.sources import JSON_API_MAX_BYTES, Job, decode_board_json

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE_NAME = "usajobs"
IS_SEARCH_SOURCE = True

# Shown in the Companies table for employers this source creates.
# Every one of them really is a federal agency.
EMPLOYER_STATUS = "federal-agency"

API_URL = "https://data.usajobs.gov/api/search"
API_HOST = "data.usajobs.gov"

CREDENTIALS_HINT = (
    "usajobs skipped - add credentials.usajobs.email and "
    "credentials.usajobs.api_key, free key at developer.usajobs.gov"
)

# The API pages at up to 500 per page; 100 keeps a single page well inside
# JSON_API_MAX_BYTES even for description-heavy postings while still
# clearing a whole agency's postings in a couple of pages.
RESULTS_PER_PAGE = 100
MAX_PAGES_PER_QUERY = 5

# Statuses that mean "your key is not usable", as opposed to "nothing
# matched". 403 is included because OPM returns it for a key that exists but
# has been revoked, not only for a malformed one.
# The SecurityClearance value that carries no information - see _description.
AMBIGUOUS_CLEARANCE = "other"

AUTH_FAILURE_STATUSES = frozenset({401, 403})
THROTTLED_STATUS = 429

# Each title_include term and each configured location becomes its own
# query (the API's Keyword match is a single phrase, not an OR of terms, so
# "want any of these titles" only works by asking once per title - same
# reasoning for locations). That is a real combinatorial cost, so both
# dimensions are capped, and so is the total combination count: a config
# with 5 terms and 5 locations would otherwise fire 25 query streams for one
# `collect` run.
MAX_KEYWORDS = 5
MAX_LOCATIONS = 5
MAX_QUERIES = 12
# What a full run of this collector can return, across every query stream -
# the CLI reports when a run hits it, same as every board collector's cap.
MAX_COLLECTED = RESULTS_PER_PAGE * MAX_PAGES_PER_QUERY * MAX_QUERIES


def _credentials(cfg: dict[str, Any]) -> dict[str, str] | None:
    creds = (cfg.get("credentials") or {}).get("usajobs") or {}
    email = str(creds.get("email") or "").strip()
    api_key = str(creds.get("api_key") or "").strip()
    if not email or not api_key:
        return None
    return {"email": email, "api_key": api_key}


def has_credentials(cfg: dict[str, Any]) -> bool:
    return _credentials(cfg) is not None


def _queries(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    search = cfg.get("search") or {}
    terms = [str(t) for t in (search.get("title_include") or []) if t][:MAX_KEYWORDS] or [""]
    locations = [str(loc) for loc in (search.get("locations") or []) if loc][:MAX_LOCATIONS] or [""]
    combos = [(term, loc) for term in terms for loc in locations]
    return combos[:MAX_QUERIES]


def _search_page(term: str, location: str, page: int, creds: dict[str, str],
                  fetcher: Callable[..., tuple[int, str, str]]) -> dict[str, Any]:
    params: dict[str, str] = {"ResultsPerPage": str(RESULTS_PER_PAGE), "Page": str(page)}
    if term:
        params["Keyword"] = term
    if location:
        params["LocationName"] = location
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    headers = {"Host": API_HOST, "User-Agent": creds["email"],
               "Authorization-Key": creds["api_key"]}
    # A documented, keyed public API, same as every board API in this
    # package - respect_robots=False for the reason JSON_API_MAX_BYTES's
    # docstring gives, not because this module is special-cased.
    status, text, _ = fetcher(url, timeout=25, max_bytes=JSON_API_MAX_BYTES,
                               respect_robots=False, headers=headers)
    # A rejected key and a throttled client must NOT read as "no federal jobs
    # matched" - that is the silent-zero failure this package has already been
    # bitten by twice (the board fetch cap, and robots.txt on board APIs). The
    # distinction matters more here than for a board because OPM disables keys
    # for inactivity (ToS section 7) and reserves the right to cap transaction
    # volume at any time (section 6), so both of these WILL eventually happen
    # to a long-lived install.
    if status in AUTH_FAILURE_STATUSES:
        raise RuntimeError(
            f"usajobs rejected the API key (HTTP {status}) - keys are disabled "
            "for inactivity and can be re-requested at developer.usajobs.gov; "
            "check credentials.usajobs.email matches the registered address")
    if status == THROTTLED_STATUS:
        raise RuntimeError(
            "usajobs is throttling this key (HTTP 429) - the search ran too "
            "many queries; reduce search.title_include or search.locations, "
            "and wait before collecting again")
    if status != 200 or not text:
        return {}
    data = decode_board_json(text)
    return data if isinstance(data, dict) else {}


def _location_text(descriptor: dict[str, Any]) -> str:
    display = str(descriptor.get("PositionLocationDisplay") or "").strip()
    if display:
        return display
    locations = descriptor.get("PositionLocation")
    if not isinstance(locations, list):
        return ""
    names = [str(loc.get("LocationName", "")).strip()
             for loc in locations if isinstance(loc, dict) and loc.get("LocationName")]
    return "; ".join(n for n in names if n)


def _description(descriptor: dict[str, Any]) -> str:
    user_area = descriptor.get("UserArea")
    details = user_area.get("Details") if isinstance(user_area, dict) else None
    details = details if isinstance(details, dict) else {}

    parts: list[str] = []
    summary = str(details.get("JobSummary") or "").strip()
    if summary:
        parts.append(summary)

    duties = details.get("MajorDuties")
    if isinstance(duties, list):
        duties_text = "\n".join(str(d).strip() for d in duties if str(d).strip())
    else:
        duties_text = str(duties or "").strip()
    if duties_text:
        parts.append(f"Major Duties:\n{duties_text}")

    qualifications = str(details.get("QualificationSummary")
                          or descriptor.get("QualificationSummary") or "").strip()
    if qualifications:
        parts.append(f"Qualifications:\n{qualifications}")

    # Job carries no salary fields (unlike every other collector's postings,
    # a federal announcement's pay is structured data, not prose) - folding
    # it into the description text is what lets screen.py's existing
    # enrich.extract_salary find it exactly like any other posting's pay.
    remuneration = descriptor.get("PositionRemuneration")
    if isinstance(remuneration, list) and remuneration and isinstance(remuneration[0], dict):
        r0 = remuneration[0]
        lo, hi = r0.get("MinimumRange"), r0.get("MaximumRange")
        if lo or hi:
            interval = str(r0.get("RateIntervalCode") or "").strip()
            line = f"Salary range: ${lo} - ${hi}"
            if interval:
                line += f" ({interval})"
            parts.append(line)

    # Vetting requirements are STRUCTURED fields here, not prose - the only
    # source in this package where that is true. Folding them in as two
    # canonical lines does double duty: the person reading the posting in the
    # app sees them, and requirements.py parses a string this module controls
    # rather than whatever wording an agency chose.
    #
    # Both are needed because they disagree. Measured over 400 live postings:
    # 50 carried a clean PositionSensitivitiy but SecurityClearance "Secret".
    # Either field alone would have passed those through.
    clearance = str(details.get("SecurityClearance") or "").strip()
    if clearance.lower() == AMBIGUOUS_CLEARANCE:
        # "Other" is the agency picking a value off the standard list - an
        # absence of information, not a stated requirement. Measured over 400
        # live postings it appeared 91 times, 67 of them alongside a CLEAN
        # PositionSensitivitiy, so reading it as "requires a clearance"
        # discarded jobs that require none. Worded so the reader still sees
        # it while requirements.py does not parse it as a requirement;
        # PositionSensitivitiy below is the field that actually decides.
        parts.append("Clearance listed as: Other (unspecified)")
    elif clearance:
        parts.append(f"Security Clearance: {clearance}")
    # USAJOBS spells this field "PositionSensitivitiy". The typo is theirs and
    # is part of the wire format, so it must be matched exactly.
    sensitivity = str(details.get("PositionSensitivitiy") or "").strip()
    if sensitivity:
        parts.append(f"Position Sensitivity: {sensitivity}")

    return "\n\n".join(parts)


def _to_job(item: dict[str, Any]) -> Job | None:
    descriptor = item.get("MatchedObjectDescriptor")
    if not isinstance(descriptor, dict):
        return None
    position_id = str(descriptor.get("PositionID") or "").strip()
    title = str(descriptor.get("PositionTitle") or "").strip()
    if not position_id or not title:
        return None
    employer = str(descriptor.get("OrganizationName")
                    or descriptor.get("DepartmentName") or "").strip()
    return Job(
        source=SOURCE_NAME,
        source_id=position_id,
        title=title,
        location=_location_text(descriptor),
        url=str(descriptor.get("PositionURI") or ""),
        posted=str(descriptor.get("PublicationStartDate") or ""),
        description=_description(descriptor),
        employer=employer or "Unknown Agency",
    )


def collect(cfg: dict[str, Any], *,
            fetcher: Callable[..., tuple[int, str, str]] = default_fetch) -> list[Job]:
    """Callers must check `has_credentials(cfg)` first and print
    `CREDENTIALS_HINT` themselves when it is False - this function is a
    plain no-op (returns []) rather than raising, so a caller that forgets
    the check still fails safe instead of a 401 surfacing as a collect
    error.
    """
    creds = _credentials(cfg)
    if creds is None:
        return []

    jobs: dict[str, Job] = {}
    for term, location in _queries(cfg):
        # Only the first page of a given query reports a trustworthy total -
        # same fact as workday.py's paging, confirmed independently against
        # this API's own SearchResultCountAll behavior.
        expected: int | None = None
        seen_this_query = 0
        for page in range(1, MAX_PAGES_PER_QUERY + 1):
            data = _search_page(term, location, page, creds, fetcher)
            result = data.get("SearchResult") if isinstance(data, dict) else None
            result = result if isinstance(result, dict) else {}
            items = result.get("SearchResultItems")
            items = items if isinstance(items, list) else []
            if not items:
                break
            if expected is None:
                total = result.get("SearchResultCountAll")
                if isinstance(total, int) and total > 0:
                    expected = total
            for item in items:
                if isinstance(item, dict):
                    job = _to_job(item)
                    if job is not None:
                        jobs[job.key()] = job
            seen_this_query += len(items)
            if len(items) < RESULTS_PER_PAGE:
                break
            if expected is not None and seen_this_query >= expected:
                break
    return list(jobs.values())
