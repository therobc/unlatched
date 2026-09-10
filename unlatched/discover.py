"""discover.py - Company name -> domain -> careers page -> ATS.

Three steps, and none of them trusts the one before it until it has been
checked:

  1. Guess candidate domains from the company name and see which ones
     resolve. Resolving is cheap evidence, not proof: a lookalike domain
     resolves just as well as the real one.
  2. Fetch a short, fixed list of conventional careers paths on the domains
     that resolved, and require the page to name the company before
     anything on it is trusted (`page_confirms_company`). A domain that
     merely exists is not the employer's site.
  3. Fingerprint whichever applicant tracking system the confirmed page
     embeds, so the right collector in `sources/` can be pointed at it.

Everything that runs over a fetched page is a plain, disjoint-class regex.
An earlier version of the careers-host pattern used overlapping
character classes and could backtrack catastrophically on a long input -
1.5s at 20KB, 109s at 160KB. Disjoint classes remove the ambiguity a
backtracking engine needs: there is exactly one way to match any given host,
so there is nothing left to try twice.
"""
from __future__ import annotations

import re
import socket
from collections.abc import Callable
from html import unescape as html_unescape
from typing import Any

from .fetch import fetch as default_fetch

FetchFn = Callable[..., tuple[int, str, str]]

COMPANY_SUFFIX = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|"
    r"holdings|group|technologies|technology|solutions|systems|labs|"
    r"software|services)\b\.?", re.IGNORECASE)

# Deliberately short. Guessing more paths costs a request per guess against
# the per-company ceiling, and `careers_links` covers the same ground far
# better: the "www.{d}" host below already fetches the homepage, and an
# employer's own navigation names its careers page exactly, whatever
# unguessable path it lives at.
CAREERS_PATHS = ("/careers", "/careers/", "/jobs", "/jobs/", "/company/careers",
                  "/about/careers", "/en/careers", "/careers/jobs")
CAREERS_HOSTS = ("careers.{d}", "jobs.{d}", "www.{d}")

# A resolving domain is not the company's domain. Short company names
# collide with unrelated registered domains constantly - one two-word
# logistics name resolved all three of bothwords.com, firstword.com and
# firstword.io, every one of them registered to somebody, and every extra
# "verified" domain multiplies the fetch budget by eleven.
# Trying the first few in TLD-major order finds the real one essentially
# always, and bounds a company that would otherwise cost 44 requests.
MAX_DOMAINS_TRIED = 3
# Hard ceiling per company, whatever the domain math produces. Discovery
# across a 40-company list has to finish in minutes; an unbounded probe is
# how it turns into hours.
MAX_URL_ATTEMPTS = 16

# Disjoint character classes: the "careers/jobs/apply/..." prefix class and
# the hostname-tail class share no characters, so there is only ever one way
# to parse a match. That is the fix for the catastrophic-backtracking defect
# - the earlier version let both classes eat ".", so the engine could
# split a long run at every position looking for a "/" that never came.
CAREERS_HOST_RE = re.compile(
    r"https?://((?:careers|jobs|apply|talent|workwith|joinus)[a-z0-9-]*"
    r"(?:\.[a-z0-9-]+)+)/", re.IGNORECASE)

GENERIC_TOKEN = {
    "the", "and", "group", "global", "national", "american", "united", "first",
    "general", "associates", "partners", "consulting", "health", "care",
    "financial", "capital", "digital", "data", "cloud", "cyber", "media",
    "industries", "enterprises", "international", "specialty", "insurance",
    "company", "corp", "corporation", "services", "solutions", "systems",
    "technology", "technologies", "management", "logistics", "labs", "studio",
    "works",
}

# One fingerprint per ATS this package can collect from. Order does not
# matter here - detect_ats tries every pattern and returns every hit.
ATS_FINGERPRINT = [
    # Five shapes, all seen live. THE FIFTH IS THE APPLICATION FORM -
    # "/embed/job_app?for=<slug>", on either host. It is what an
    # aggregator's "original job post" link points at, because that is the
    # page a candidate applies on rather than the employer's board index.
    # Somebody handing this app the link they were given got nothing back:
    # the slug sits in the query string and was not read. Measured
    # 2026-09-02 against a live link of exactly that shape.
    #
    # The earlier pattern covered two of the five, which silently cost
    # employers this app can fully collect from: one publishes
    # ".../embed/job_board/js?for=<slug>" and another the bare
    # "boards.greenhouse.io/<slug>", and both were recorded as having no
    # board at all.
    #
    # `(?!embed\b)` on the bare-slug branch stops it capturing "embed" as a
    # company slug when it meets an embed URL the first branch should own.
    ("greenhouse", re.compile(
        r"(?:job-)?boards\.greenhouse\.io/embed/"
        r"(?:job_board(?:/js)?|job_app)\?for=([\w-]+)|"
        r"(?:job-)?boards\.greenhouse\.io/(?!embed\b)([\w-]+)", re.IGNORECASE)),
    ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)", re.IGNORECASE)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w-]+)", re.IGNORECASE)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.IGNORECASE)),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([\w-]+)", re.IGNORECASE)),
    ("recruitee", re.compile(r"([\w-]+)\.recruitee\.com", re.IGNORECASE)),
    ("bamboohr", re.compile(r"([\w-]+)\.bamboohr\.com", re.IGNORECASE)),
    ("breezy", re.compile(r"([\w-]+)\.breezy\.hr", re.IGNORECASE)),
    # The optional segment before the site is a LOCALE and nothing else.
    # Written as `(?:[\w-]+/)?` it happily ate the site itself whenever the
    # URL carried no locale - "myworkdayjobs.com/CVS_Health_Careers/job/..."
    # captured "job", and /wday/cxs/cvshealth/job/jobs returns nothing at
    # all. Measured across 34 Workday employers on 2026-08-06: 20 of them
    # resolved to a board that could never return a posting, and every one
    # of those users would have seen an employer that simply never has any
    # openings.
    ("workday", re.compile(
        r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/"
        r"(?:[a-z]{2}[-_][A-Za-z]{2,4}/)?"
        r"([\w-]+)", re.IGNORECASE)),
    # Oracle Fusion Cloud Recruiting: every tenant is its own pod host
    # ("{tenant}.fa.{pod}.oraclecloud.com", e.g. "tenant.fa.us2.oraclecloud.com"),
    # linking to a candidate experience site keyed by an opaque site id that
    # is NOT always "CX_<number>" - one tenant's is the bare slug "CX", and
    # another's is a custom name of the employer's own choosing. Capturing
    # whatever site id the company's own page actually links to is why the
    # second group exists, rather than assuming a numbering scheme.
    ("oracle_hcm", re.compile(
        r"https?://([\w-]+\.fa\.[\w-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/"
        r"[a-z-]+/sites/([\w-]+)", re.IGNORECASE)),
    # Same platform, weaker evidence: many careers pages reference the
    # Oracle host without the candidate-experience path that names the
    # site. Two of three real employers checked were only reachable this
    # way, so matching the host alone is what makes the collector usable;
    # the site is resolved at collection time instead.
    ("oracle_hcm", re.compile(
        r"([\w-]+\.fa\.[\w-]+\.oraclecloud\.com)", re.IGNORECASE)),
]


def page_confirms_company(html: str, company: str) -> bool:
    """Does this page actually belong to the company we asked about?

    A domain resolving proves it exists, not that it is theirs. Confirmation
    means a distinctive token of the company name appears on the page.
    Generic words are excluded so a name assembled only from them - a real
    example reaching here had three words, each one of them already on the
    list above - has to match on something better than any single one.
    """
    if not html:
        return False
    low = html.lower()
    toks = [t for t in re.findall(r"[a-z0-9]{4,}", (company or "").lower())
            if t not in GENERIC_TOKEN]
    if not toks:
        # A name whose only distinctive part is an initialism and whose other
        # words are all generic - "NWS Financial Services", "FBR Logistics",
        # "TSP Supply Chain Solutions". Every 4+ token is generic, so the
        # rule above finds nothing, and squashing the WHOLE name looks for
        # "nwsfinancialservices", a string that appears on no page anywhere.
        # One such employer's own careers page - 157KB, carrying its initials
        # throughout - was rejected exactly this way and recorded as dead.
        #
        # Three characters, as a whole word, matched individually. Two would
        # be too loose to be evidence of anything ("at" appears on every
        # page ever written); AT&T is still reached by the squashed form
        # below, which is what already carries the ordinary three-letter
        # initialisms.
        words = re.findall(r"[a-z0-9]+", (company or "").lower())
        for short in (w for w in words if len(w) == 3 and w not in GENERIC_TOKEN):
            if re.search(r"\b" + re.escape(short) + r"\b", low):
                return True
        whole = re.sub(r"[^a-z0-9]", "", (company or "").lower())
        if len(whole) >= 2:
            return re.search(r"\b" + re.escape(whole) + r"\b", low) is not None
        return False
    squashed = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if len(squashed) >= 8 and squashed in re.sub(r"[^a-z0-9]", "", low):
        return True
    return any(t in low for t in toks)


def candidate_domains(company: str) -> list[str]:
    """Domain guesses, ordered TLD-major: every stem's .com before any .io
    or .co. A lookup costs a DNS query, and .com is right the overwhelming
    majority of the time - stem-major ordering buries the likely answer
    behind guesses that will never resolve.
    """
    base = COMPANY_SUFFIX.sub("", company or "").strip()
    base = re.sub(r"[^a-z0-9\s-]", "", base.lower()).strip()
    if len(base) < 2:
        return []
    squashed = re.sub(r"[\s-]+", "", base)
    hyphen = re.sub(r"\s+", "-", base)

    words = base.split()
    stems = [squashed, hyphen]
    stems.extend("".join(words[:n]) for n in (2, 1) if len(words) > n)

    out = []
    for tld in (".com", ".io", ".co"):
        for stem in stems:
            if len(stem) < 3:
                continue
            d = stem + tld
            if d not in out:
                out.append(d)
    return out[:10]


def resolves(domain: str, timeout: float = 5.0) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname(domain)
        return True
    except (OSError, UnicodeError):
        # OSError covers DNS failure (gaierror) and lookup timeout; UnicodeError
        # covers a domain string that fails IDNA encoding. Neither means the
        # company has no site - just that this particular guess is not it.
        return False
    finally:
        socket.setdefaulttimeout(None)


def html_to_text(raw: str) -> str:
    """HTML -> text, keeping paragraph breaks. Collapsing every whitespace
    run to a single space destroys the line structure downstream parsing
    relies on, so block tags become newlines and only spaces/tabs collapse.
    """
    if not raw:
        return ""
    t = html_unescape(raw)
    t = re.sub(r"(?i)<\s*br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol|section)\s*>", "\n", t)
    t = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return t.strip()


# Workday path segments that are part of its own navigation rather than a
# career site: a link to the signed-in landing page or a login screen names
# the tenant but not a site anything can be collected from. Recording one as
# the site produces an employer that is permanently, silently empty, which
# is worse than recording no board at all - the person adds the employer,
# sees no jobs, and concludes the employer is not hiring.
WORKDAY_NON_SITES = {"job", "jobs", "login", "userhome", "home", "page",
                     "details", "apply", "search", "signin", "register"}


def detect_ats(html: str) -> list[dict[str, Any]]:
    found = []
    for name, rx in ATS_FINGERPRINT:
        for m in rx.finditer(html or ""):
            groups = [g for g in m.groups() if g]
            if name == "workday" and len(groups) >= 3 and \
                    groups[2].lower() in WORKDAY_NON_SITES:
                # Keep looking: the same page usually links the real career
                # site as well as its login, and taking only the first match
                # meant whichever came first in the HTML won.
                continue
            found.append({"provider": name, "parts": groups})
            break
    return found


# Words that mark a link as pointing at a careers section, checked against
# both the href and the anchor text. Text matters as much as the URL: plenty
# of employers label the link "Careers" while the href is /en/about/join-us,
# which no path guess would ever produce.
CAREERS_LINK_HINT = re.compile(
    r"career|job|opening|vacanc|employment|hiring|"
    r"join[- _]?(?:us|our|the)|work[- _]?(?:with|for|at)[- _]?us|opportunit",
    re.IGNORECASE)

# How many of a page's own careers links to follow. A large site can link
# dozens of loosely-matching URLs; the real careers entry point is
# essentially always among the first few.
MAX_CAREERS_LINK_HOPS = 4

# Scanned per anchor. Long enough for an href plus its label, short enough
# that no amount of input turns this into a backtracking problem - the same
# concern the module docstring records for CAREERS_HOST_RE.
_ANCHOR_WINDOW = 400


def _is_root(url: str) -> bool:
    """A bare domain with no path - what discovery falls back to recording
    when no careers page is found. Worth replacing with anything better.
    """
    return re.sub(r"^https?://[^/]+", "", url or "") in ("", "/")


def careers_links(html: str, base_url: str) -> list[str]:
    """Absolute URLs on the SAME site that look like a careers section.

    Complements external_careers_hosts, which only finds careers portals on
    OTHER domains. The gap this closes: 83 of 104 unreadable employers had
    nothing recorded but their homepage, because every guessed path missed
    and there was no route from a confirmed page to the careers section it
    links to in its own navigation.

    Parsed by splitting on anchor starts rather than one regex over the
    whole document, so a hostile or merely enormous page cannot make this
    expensive.
    """
    if not html:
        return []
    m_base = re.match(r"(https?://[^/]+)", base_url or "")
    if not m_base:
        return []
    origin = m_base.group(1)

    out: list[str] = []
    for chunk in html.split("<a ")[1:]:
        window = chunk[:_ANCHOR_WINDOW]
        m_href = re.search(r"""href=["']([^"'>\s]+)["']""", window)
        if not m_href:
            continue
        href = html_unescape(m_href.group(1))
        # The label sits after the tag closes; missing or empty is fine,
        # the href alone can still qualify the link.
        label = window.split(">", 1)[1] if ">" in window else ""
        label = re.sub(r"<[^>]*>", " ", label)[:120]
        if not (CAREERS_LINK_HINT.search(href) or CAREERS_LINK_HINT.search(label)):
            continue
        if href.startswith("//"):
            url = "https:" + href
        elif href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = origin + href
        else:
            continue
        # Same origin only. Other domains are external_careers_hosts' job,
        # and following arbitrary offsite links is how a crawler wanders.
        if url.startswith(origin) and url not in out:
            out.append(url)
        if len(out) >= MAX_CAREERS_LINK_HOPS:
            break
    return out


def external_careers_hosts(html: str) -> list[str]:
    """Hosts on OTHER domains that look like a careers portal - a subsidiary
    often lists nothing itself and points at a parent group's board.
    """
    out = []
    for raw_host in CAREERS_HOST_RE.findall(html or ""):
        h = raw_host.lower().strip(".")
        if h and h not in out:
            out.append(h)
    return out


# Providers whose reference is a COMPOUND of several parts rather than a
# single string. Measured against the shipped pack: every Workday ref is three
# parts (ghr|wd1|lateral-us, citi|wd5|2) and Oracle is a host with an optional
# site (eiqg.fa.us2.oraclecloud.com|CX_1001, or the bare host). Getting this
# wrong does not fail loudly - it stores a ref the collector cannot use, and
# the employer simply returns nothing from then on.
COMPOUND_REF = ("workday", "oracle_hcm")


def ats_of(res: dict[str, Any]) -> tuple[str, str]:
    """(provider, ats_ref) from a resolve() result, or ("", "") for none.

    THE FIRST ENTRY WINS, which is what cmd_discover has always done. Worth
    stating precisely, because "first" does not mean what it looks like:
    detect_ats walks the ATS_FINGERPRINT table and appends at most one match
    per FINGERPRINT, so the order is the order that TABLE declares, not the
    order the fingerprints appear in the page. A careers page matching two
    providers is usually one real board plus another's widget, and which one
    wins is therefore a property of the table, not of the employer's HTML.

    NOT ONE PER PROVIDER - oracle_hcm has two fingerprints, and a page carrying
    the full candidate-experience URL matches both, returning
    ("...oraclecloud.com", "CX_1001") followed by the bare host on its own.
    Entry zero is the richer of the two ONLY because the specific row is
    declared before the fallback row, so that ordering in ATS_FINGERPRINT is
    load-bearing: reversing it would quietly start storing a ref with no site
    id for every Oracle employer that publishes one.

    Lives here rather than in a caller because two commands now ask this same
    question of this same structure - `discover` when it records a company,
    and `rediscover` when it checks whether the answer has changed. Two copies
    of one rule is what this codebase keeps getting bitten by.
    """
    if not res.get("ats"):
        return "", ""
    first = res["ats"][0]
    provider = str(first.get("provider") or "")
    parts = list(first.get("parts") or [])
    if not provider:
        return "", ""
    if provider in COMPOUND_REF:
        return provider, "|".join(parts)
    return provider, (parts[0] if parts else "")


def resolve(company: str, *, fetcher: FetchFn = default_fetch) -> dict[str, Any]:
    """Name -> confirmed careers page -> ATS fingerprint(s).

    Returns {"company", "domain", "careers_url", "ats": [...], "portals": [...],
    "note"}. Never fetches anything from a domain that did not resolve, and
    never trusts a page that did not confirm the company.
    """
    res: dict[str, Any] = {"company": company, "domain": "", "careers_url": "",
                            "ats": [], "portals": [], "note": ""}

    verified = []
    for d in candidate_domains(company):
        if resolves(d):
            verified.append(d)
        if len(verified) >= MAX_DOMAINS_TRIED:
            break
    if not verified:
        res["note"] = "no domain resolved"
        return res

    # Grouped by domain, so the ceiling below spends its budget finishing
    # the most likely domain rather than sampling the front of each.
    by_domain: list[tuple[str, list[str]]] = []
    for d in verified:
        urls = [f"https://{h.format(d=d)}" for h in CAREERS_HOSTS]
        urls += [f"https://{d}{p}" for p in CAREERS_PATHS]
        by_domain.append((d, urls))

    confirmed = False
    portals: list[str] = []
    own_links: list[str] = []
    attempts = 0
    for domain, urls in by_domain:
        # A page that named the company settles which domain is theirs.
        # Continuing on to another candidate's URLs after that only spends
        # requests on domains already known to be the wrong ones.
        if confirmed and res["domain"] and res["domain"] != domain:
            break
        for url in urls:
            if attempts >= MAX_URL_ATTEMPTS:
                break
            attempts += 1
            status, html, final = fetcher(url)
            if status != 200 or len(html) < 500:
                continue
            if not page_confirms_company(html, company):
                continue
            confirmed = True
            m_host = re.match(r"https?://([^/]+)", final or url)
            if m_host:
                host = m_host.group(1).lower()
                for cand in verified:
                    if cand in host:
                        res["domain"] = cand
                        break
            ats = detect_ats(html)
            if ats:
                res["careers_url"], res["ats"] = final, ats
                break
            if not res["careers_url"]:
                res["careers_url"] = final
            portals += external_careers_hosts(html)
            # Keep the page that confirmed the company. Its own navigation is
            # the most reliable route to the careers section - better than any
            # path guess, because the employer wrote the link themselves.
            if not own_links:
                own_links = careers_links(html, final or url)
        if res["ats"] or attempts >= MAX_URL_ATTEMPTS:
            break

    # Follow the confirmed page's OWN careers links before giving up. This is
    # the step whose absence left 83 of 104 unreadable employers recorded as
    # nothing but a homepage: every guessed path missed, and the "Careers"
    # link sitting in their navigation was never followed. Runs before the
    # corporate-portal hop because an employer's own careers page is more
    # likely to name its ATS than a parent group's landing page.
    if confirmed and not res["ats"]:
        for link in own_links[:MAX_CAREERS_LINK_HOPS]:
            status, html, final = fetcher(link)
            if status != 200 or len(html) < 500:
                continue
            ats = detect_ats(html)
            if ats:
                res["careers_url"], res["ats"] = final, ats
                res["note"] = "via careers link on the confirmed page"
                break
            # Even without a fingerprint this beats the homepage: a real
            # careers page is what the schema.org and sitemap fallbacks need
            # to have any chance, and what a person would want to open.
            if _is_root(res["careers_url"]):
                res["careers_url"] = final
            portals += external_careers_hosts(html)

    # One hop to a corporate careers portal linked from a confirmed page - a
    # subsidiary's own site rarely hosts the board itself.
    if confirmed and not res["ats"]:
        for host in list(dict.fromkeys(portals))[:3]:
            registrable = host.split(".", 1)[-1] if host.count(".") > 1 else host
            if not resolves(registrable):
                continue
            status, html, final = fetcher(f"https://{host}/")
            if status != 200 or len(html) < 500:
                continue
            ats = detect_ats(html)
            if ats:
                res["careers_url"], res["ats"] = final, ats
                res["note"] = f"via corporate portal {host}"
                break

    res["portals"] = list(dict.fromkeys(portals))[:3]
    if not confirmed:
        res["careers_url"] = ""
        res["note"] = "domain resolved but no page identified itself as this company"
    elif not res["ats"]:
        # No recognised ATS. collect() still has a route for this: it tries
        # the confirmed careers page as schema.org JobPosting markup, then
        # the host's sitemaps. That decision lives in cli.py so there is
        # exactly ONE place that maps a company to a collector.
        res["note"] = ("careers page confirmed, but no ATS fingerprint - "
                        "collect falls back to schema.org, then sitemap")
    return res
