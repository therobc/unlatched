"""config.py - Load, validate and save config.json.

Every key is optional. A user who never touches config.json still gets a
working (if unfiltered) tool - constraint: nothing in the scoring path may
require a model, and nothing in the search path may require the CLI to have
been configured first. `defaults()` is the schema documentation as much as it
is a default value.
"""
from __future__ import annotations

import copy
import json
import os
from typing import TYPE_CHECKING, Any, cast

from . import employment as employment_mod
from . import keystore as keystore_mod
from . import paths
from . import requirements as requirements_mod

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The full shape. Every field a user or the desktop UI can set lives here, so
# there is exactly one place that documents the config schema.
DEFAULTS: dict[str, Any] = {
    "search": {
        "terms": [],
        "title_include": [],
        "title_exclude": [],
        "seniority": [],
        # Places the person can actually work, e.g. ["Knoxville, TN",
        # "Maryville, TN"] or a bare state ["TN"]. Only applied to postings
        # that are not remote. Empty means no location filtering at all.
        "locations": [],
        # Accept employers based in one of those locations who send people
        # out to job sites. Only consulted for postings whose own location
        # is unclear.
        "travel_ok": False,
        # Keep only postings the person can actually work from the US.
        # Remoteness says nothing about jurisdiction: "Remote - India" is a
        # remote job in India, and it passed a remote-only search untouched.
        # 41 of 133 matches in a real US search were foreign before this.
        # Turn off for a search that spans countries.
        "us_only": True,
        "salary_floor": None,
        # Pay below salary_floor but at or above this is recorded as an "alt"
        # match: shown, flagged, and distinguished from a clean keep. Null
        # disables the tier entirely, so anything under the floor drops.
        "salary_alt_floor": None,
        # Employment types the person will take, from employment.KINDS.
        # Empty accepts everything. A posting of another type is never
        # dropped - it is flagged "alt" so the person can dismiss it in
        # triage themselves, because a job they never saw cannot be
        # recovered.
        "employment_types": [],
        "currency": "USD",
        # Which of remote / hybrid / onsite the person will take. Empty
        # accepts all three, the same way an empty employment_types does.
        # Hybrid is its own answer rather than a shade of remote: a hybrid
        # posting nearly always says "remote" somewhere, and reading that as
        # remote is how somebody who cannot commute ends up applying for a
        # job that expects them in the office three days a week.
        "work_modes": [],
        # Superseded by work_modes, and read only when work_modes is empty so
        # a config written before the three ticks existed keeps its search.
        "remote_scope": "any",
    },
    "skills": [],
    "resume_path": None,
    "sources": {
        "greenhouse": True,
        "lever": True,
        "ashby": True,
        "smartrecruiters": True,
        "workable": True,
        "recruitee": True,
        "workday": True,
        "oracle_hcm": True,
        "bamboohr": True,
        "breezy": True,
        "schema_org": True,
        "sitemap": True,
        "usajobs": True,
        "remoteok": True,
        "nodesk": True,
    },
    # ONE setting, and it is about a thing the person does, not about a site.
    #
    # Naming a setting after one site tells the reader there is an integration
    # with that site, and becomes a lie the moment a second site joins the same
    # category (decided 2026-08-08). LinkedIn is not the category. It is the
    # current sole member of one - sites this app reads ONLY when somebody is
    # deliberately adding a single job by hand - and it is what that path is
    # tested against because it is the most popular.
    #
    # `max_bytes`, `timeout_s`, `per_host_delay_s` and `respect_robots` used to
    # sit here too. Nothing read any of them: every collector passes its own
    # values. `respect_robots` was the worst of the four, because it could only
    # ever mislead - whether robots applies is decided per endpoint by what the
    # endpoint IS (a published API is consent; somebody's HTML is not), so a
    # global switch could not have loosened or tightened anything. Removed
    # rather than wired, 2026-08-08.
    "fetch": {
        # Go and read the page when a job is added by link, filling in the
        # title, employer and description instead of making the person type
        # them. Off means they type.
        #
        # This is also the switch that decides whether the app reads the sites
        # in manual.ATTENDED_ONLY_HOSTS - the ones whose robots.txt asks
        # automated tools not to, which this app honours everywhere EXCEPT on
        # this one deliberate, person-present, one-page-at-a-time path. Never a
        # collect, never the scheduled refresh. No login, no cookies, this
        # app's own honest user agent.
        #
        # SHIPPED OFF. The first user's decision, 2026-08-08, and it is about whose name
        # is on the repository rather than about whether the behaviour is
        # defensible. Reading one page a person is looking at is not crawling -
        # that argument stands - but this ships public and MIT under the first user's
        # name, and "a user turned that on" is a materially different position
        # from "the author shipped it on".
        #
        # So the default is off and the app says so in three places rather than
        # failing quietly: a standing line next to Refresh while it is off, a
        # step in the first-run walkthrough showing where the switch is, and a
        # prompt the first time somebody adds a link and finds nothing filled
        # in. A setting that silently does nothing is how you get a person
        # concluding the feature is broken.
        #
        # An existing install that had it ON keeps it ON - see RENAMED_KEYS and
        # the merge in load(). This changes what a NEW config starts with.
        "read_added_links": False,
    },
    "agent_api": {
        "base_url": None,
        "api_key": None,
        "model": None,
    },
    # An optional handoff path. Point it at a JSON file in the same shape
    # `import` accepts and the daily refresh takes those rows in BEFORE it
    # collects, so they are present when the dedupe runs. This is the same
    # import path the verb uses; it fetches nothing.
    #
    # SHIPS OFF, and null IS the off switch - no path, no behaviour, nothing
    # scheduled. Kept rather than cut because the code is small, it is exercised
    # by the test suite either way, and removing a feature to re-add it later
    # costs more than leaving it dormant. Anyone who wants it sets a path.
    #
    # A FILE RATHER THAN A SECOND PROCESS CALLING `import`: that arrangement
    # lets the sender write to this database on the sender's schedule, so
    # whether rows arrive depends on when that process ran. Reading a file puts
    # the timing on this side, where the schedule already lives.
    #
    # Whoever writes it must do so ATOMICALLY - temp file in the SAME DIRECTORY,
    # then rename - or a refresh landing mid-write reads half a file. The same
    # directory is not incidental: rename is only atomic within a volume, so a
    # temp file on another drive degrades to a copy and reintroduces the window
    # it was meant to close. Writing in place is never safe here.
    "ingest": {
        "path": None,
    },
    # Per-source credentials, keyed by source name. USAJOBS is the only
    # source that needs one today: a free key from developer.usajobs.gov,
    # tied to the email address used to register for it (USAJOBS requires
    # that same email back as the `User-Agent` header on every search
    # request). `unlatched config set credentials.usajobs.api_key VALUE`
    # (and `.email`) is how a user sets these - see cli.py's `config set`
    # for the dotted-key setter this walks through.
    # Daily refresh, ON by default and switchable off per search.
    # Decided 2026-08-05: setting up or changing a search and pressing Search is
    # the deliberate act; once a search exists, keeping it current is what
    # the person already asked for. Staleness is the failure mode that
    # matters - a board checked weekly surfaces roles already days into
    # their applicant pile. See refresh.py for why the anchor is the DAY and
    # a late-morning hour rather than a timer.
    "refresh": {
        "daily": True,
        # Times of day to refresh at. The morning slot catches the main
        # 8:00-10:30 a.m. batch once it has landed; the afternoon slot
        # catches roles approved during the day, which would otherwise not
        # be seen until tomorrow. Two is deliberate - boards change in daily
        # batches, so a third run mostly re-fetches.
        # KEEP IN STEP WITH refresh.DEFAULT_ANCHORS. These are two independent
        # copies of the same decision - this one is what a new config gets
        # written with, that one is the fallback when a config has no "at" at
        # all - and changing only one leaves them disagreeing (2026-08-12).
        "at": ["11:00", "16:30"],
        # Saturday and Sunday get ONE run rather than two, later in the day
        # because weekend arrivals are not staged to a business-hours release.
        # Configurable because it was not, and the Config screen consequently
        # showed weekday times while the weekend quietly used a hardcoded
        # 11:30 - a setting that displays one thing and does another, on the
        # two days a week nobody is checking (decided 2026-08-09).
        "weekend_at": ["11:30"],
        # Weekends get ONE run rather than none: 1.7% of postings is small,
        # but a Sunday posting is already two days old before a weekdays-only
        # search sees it. Tick this to switch that single run off.
        "weekdays_only": False,
    },
    "credentials": {
        "usajobs": {
            "email": "",
            "api_key": "",
        },
    },
    # The candidate, in requirements.py's own vocabulary - what
    # requirements.compare() checks extracted posting requirements against.
    # Every scalar defaults to None (not configured, never compared) rather
    # than a guessed value; see requirements.compare for why null and an
    # empty list both read as "unknown" here, not "confirmed zero".
    "profile": {
        # Years of relevant experience the candidate has.
        "years_experience": None,
        # Highest completed level: one of requirements.EDUCATION_LEVELS
        # ("none" means the candidate has explicitly confirmed they hold no
        # formal credential, which is a fact - distinct from this whole key
        # being None, which means the question was never answered).
        "education": None,
        # Licenses/certifications held, in requirements.py's naming (e.g.
        # "CDL", "BLS") so a direct match against extracted license names
        # works without a separate alias table.
        "licenses": [],
        # Willing to take on travel? None means not configured.
        "can_travel": None,
        # Shift types the candidate will accept, from requirements.SHIFT_KINDS.
        "willing_shifts": [],
        # Willing to supervise/manage other people? None means not configured.
        "supervises_ok": None,
        # Federal vetting the candidate will accept. Two flags, not one,
        # because these are separate systems: a security clearance is a
        # national-security determination, while public trust is a
        # suitability tier (Moderate/High Risk) that is considerably easier
        # to obtain. Someone can reasonably accept one and refuse the other.
        # None means not configured and never filters; False excludes any
        # posting stating that requirement.
        "clearance_ok": None,
        "public_trust_ok": None,
    },
}

# "any" is neutral: it neither filters nor rewards. "prefer_remote"
# shows everything and ranks remote work higher, which is what most
# people mean when they say they want remote but would consider
# otherwise. Only "remote_only" actually excludes.
VALID_REMOTE_SCOPE = {"any", "prefer_remote", "remote_only", "onsite_ok"}
# Kept in step with screen.WORK_MODES, which is where the meaning lives.
VALID_WORK_MODES = {"remote", "hybrid", "onsite"}


def defaults() -> dict[str, Any]:
    # json.loads() is typed Any in the stdlib stubs since its return shape
    # depends on the input text; the cast states what a round-trip of
    # DEFAULTS (a dict[str, Any] itself) is actually guaranteed to produce.
    return cast("dict[str, Any]", json.loads(json.dumps(DEFAULTS)))


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def validate(cfg: dict[str, Any]) -> list[str]:
    """Return a list of problems (empty = ok). Never raises - config is user
    data and a malformed field should be reported, not crash the tool.
    """
    problems = []
    search = cfg.get("search") or {}
    scope = search.get("remote_scope")
    if scope is not None and scope not in VALID_REMOTE_SCOPE:
        problems.append(
            f"search.remote_scope {scope!r} not in {sorted(VALID_REMOTE_SCOPE)}")
    modes = search.get("work_modes")
    if modes is not None:
        if not isinstance(modes, list):
            problems.append("search.work_modes must be a list")
        else:
            problems.extend(
                f"search.work_modes {m!r} not in {sorted(VALID_WORK_MODES)}"
                for m in modes if m not in VALID_WORK_MODES)
    floor = search.get("salary_floor")
    if floor is not None and not isinstance(floor, int | float):
        problems.append("search.salary_floor must be a number or null")
    alt_floor = search.get("salary_alt_floor")
    if alt_floor is not None and not isinstance(alt_floor, int | float):
        problems.append("search.salary_alt_floor must be a number or null")
    elif (alt_floor is not None and isinstance(floor, int | float)
            and alt_floor >= floor):
        # An alt floor at or above the real floor describes no band at all -
        # every posting is either above the floor or below both.
        problems.append(
            f"search.salary_alt_floor ({alt_floor}) must be BELOW "
            f"search.salary_floor ({floor}) to describe a fallback band")
    problems.extend(
        f"search.{key} must be a list"
        for key in ("terms", "title_include", "title_exclude", "seniority")
        if not isinstance(search.get(key, []), list))
    types = search.get("employment_types") or []
    if not isinstance(types, list):
        problems.append("search.employment_types must be a list")
    else:
        bad = [t for t in types if t not in employment_mod.KINDS]
        if bad:
            problems.append(
                f"search.employment_types {bad} not in {sorted(employment_mod.KINDS)}")
    if not isinstance(cfg.get("skills", []), list):
        problems.append("skills must be a list")

    profile = cfg.get("profile") or {}
    years = profile.get("years_experience")
    if years is not None and not isinstance(years, int | float):
        problems.append("profile.years_experience must be a number or null")
    education = profile.get("education")
    if education is not None and education not in requirements_mod.EDUCATION_LEVELS:
        problems.append(
            f"profile.education {education!r} not in "
            f"{sorted(requirements_mod.EDUCATION_LEVELS)}")
    problems.extend(
        f"profile.{key} must be a list"
        for key in ("licenses", "willing_shifts")
        if not isinstance(profile.get(key, []), list))
    shifts = profile.get("willing_shifts") or []
    if isinstance(shifts, list):
        bad = [s for s in shifts if s not in requirements_mod.SHIFT_KINDS]
        if bad:
            problems.append(
                f"profile.willing_shifts {bad} not in {sorted(requirements_mod.SHIFT_KINDS)}")
    for key in ("can_travel", "supervises_ok", "clearance_ok", "public_trust_ok"):
        v = profile.get(key)
        if v is not None and not isinstance(v, bool):
            problems.append(f"profile.{key} must be true, false, or null")
    return problems


# Config values that are secrets, as dotted paths. `load` returns these in
# the clear and `save` protects them on the way out, so every caller in the
# package works with a plain string and only the file on disk is wrapped.
SECRET_KEYS = ("credentials.usajobs.api_key", "agent_api.api_key")


def _map_secrets(cfg: dict[str, Any], fn: Callable[[str], str]) -> dict[str, Any]:
    """Walks to each secret and rewrites it in place. A path that is absent
    (an older config.json predating the field) is skipped rather than
    created - `get_key` raises on a missing path, so this does its own
    tolerant walk instead.
    """
    for dotted in SECRET_KEYS:
        parts = dotted.split(".")
        node: Any = cfg
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if not isinstance(node, dict):
            continue
        current = node.get(parts[-1])
        if isinstance(current, str) and current:
            node[parts[-1]] = fn(current)
    return cfg


# Settings that have been renamed, old dotted path -> new one.
#
# A rename without this is a silent reset: the old key stops being read, the
# new one is absent from the file, and the person's setting quietly reverts to
# whatever the default happens to be. Somebody who had deliberately turned
# LinkedIn OFF would have found it back ON after an update, which is the worst
# direction for that particular setting to move on its own.
# Applied IN ORDER, so a chain works: a config still carrying the original
# name is walked forward one hop at a time to the current one.
RENAMED_KEYS = (
    ("fetch.manual_fetch_linkedin", "fetch.added_links_include_linkedin"),
    ("fetch.added_links_include_linkedin", "fetch.read_added_links"),
)


def _apply_renames(raw: dict[str, Any]) -> dict[str, Any]:
    """Carry a renamed setting's value across, then drop the old key.

    An explicit value under the NEW name always wins: it is the more recent
    statement of intent, and a stale copy of the old key left in a
    hand-edited file must not override it.
    """
    for old, new in RENAMED_KEYS:
        *old_path, old_leaf = old.split(".")
        node: Any = raw
        for part in old_path:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if not isinstance(node, dict) or old_leaf not in node:
            continue
        value = node.pop(old_leaf)
        *new_path, new_leaf = new.split(".")
        target: Any = raw
        for part in new_path:
            if not isinstance(target.get(part), dict):
                target[part] = {}
            target = target[part]
        target.setdefault(new_leaf, value)
    return raw


def load(home: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = paths.config_path(home)
    if not path.exists():
        return defaults()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults()
    if not isinstance(raw, dict):
        return defaults()
    return _map_secrets(
        _deep_merge(defaults(), _apply_renames(raw)), keystore_mod.unprotect
    )


def save(cfg: dict[str, Any], home: str | os.PathLike[str] | None = None) -> Path:
    path = paths.config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Protect a COPY: the caller keeps holding the config it passed in, and
    # swapping its live secret for a blob under it would break any caller
    # that saves and then keeps working (which `config set` does).
    on_disk = _map_secrets(copy.deepcopy(cfg), keystore_mod.protect)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(on_disk, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def get_key(cfg: dict[str, Any], dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def _coerce(existing: Any, raw_value: str) -> Any:
    """Turn a CLI string into the type already living at that key.

    A CLI is text in, text out; config.json is typed. Coercing against the
    CURRENT value (rather than guessing from the string alone) is what lets
    `config set search.salary_floor 70000` produce an int and
    `config set search.title_include "a,b"` produce a list, without a second
    schema to keep in sync.
    """
    if raw_value.lower() in ("null", "none", "~") and not isinstance(existing, str):
        return None
    if isinstance(existing, bool):
        return raw_value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(existing, int) and not isinstance(existing, bool):
        try:
            return int(raw_value)
        except ValueError:
            return raw_value
    if isinstance(existing, float):
        try:
            return float(raw_value)
        except ValueError:
            return raw_value
    if isinstance(existing, list):
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        if not raw_value.strip():
            return []
        return [p.strip() for p in raw_value.split(",") if p.strip()]
    if existing is None:
        # No type to anchor to. Try JSON first (numbers, lists, null, bool),
        # then fall back to a plain string.
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value
    return raw_value


def set_key(cfg: dict[str, Any], dotted: str, raw_value: str) -> dict[str, Any]:
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    last = parts[-1]
    node[last] = _coerce(node.get(last), raw_value)
    return cfg


def flatten(cfg: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """dotted-key -> value, for `config list`."""
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out
