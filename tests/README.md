# The test suite

An index of what is covered, and why each area exists.

```
pip install -e ".[dev]"
pytest                 # all of it, under a minute
pytest tests/test_dupes.py -v
pytest -k repost
```

## Why there are so many

Unlatched makes claims that are easy to say and hard to believe: that scoring
is deterministic, that no model sees a posting, that it will not read sites
whose terms forbid it, that nothing you have touched is ever deleted. A
claim in a README is marketing. The same claim with a test behind it is
something you can check in a minute without trusting anybody.

So the suite is not here to hit a coverage number. Most of these files exist
because something was wrong once, and the test is what makes sure it stays
fixed. Where a test's docstring says what went wrong, that is a real incident.

**No test touches the network.** `conftest.py` blocks name resolution for the
whole run, and `test_no_network_guard.py` tests the guard itself - because a
network block nobody checks is a network block that quietly stopped working.

## What is covered, by area

### What the app decides about a job
Screening is the heart of it: whether a posting is worth your attention, and
why. These cover the vocabulary and every edge that has bitten it.

`test_verdicts` `test_alt_reason_migration` `test_robots`
`test_requirements`
`test_requirements_summary`
`test_title_word_boundaries` `test_employment_types` `test_work_modes`
`test_country` `test_location` `test_salary_floor_range_top`
`test_benefits_not_salary` `test_remote_positive_evidence` `test_criteria`
`test_coverage_vocabulary` `test_inflections` `test_case_normalization`
`test_df_cap` `test_job_gaps` `test_keywords` `test_keyword_mining`

A flavour of what that means in practice: a dollar amount inside a benefits
sentence is not pay; a salary floor is judged against the top of a posted
range; "Benefit Coordinator" must not match a search for "benefits
coordinator" by accident; and a posting that never mentions location is not
thereby remote.

### Reading employers' boards
One file per board system, plus the machinery that finds and paces them.

`test_bamboohr_detail` `test_lever_dates` `test_nodesk` `test_oracle_hcm`
`test_remoteok` `test_usajobs` `test_workday_paging` `test_starter`
`test_collect_fallback` `test_collect_by_origin` `test_confirm_company`
`test_company_origin` `test_discovery_budget` `test_pacing`
`test_rediscover` `test_self_healing` `test_agent_api`
`test_board_fetch_cap` `test_fetch_cap` `test_federal_vetting`
`test_title_prefilter`

Board APIs lie in specific ways, and each of those is a test: Lever reports
epoch milliseconds, Workday's per-page total cannot be trusted, BambooHR needs
a second call per job and that is where the detail lives.

### Trust boundaries
The parts where being wrong costs more than a missed job. Several came out of
a red-team review in August 2026.

`test_link_safety` `test_redirect_policy` `test_regex_adversarial`
`test_gate_rejects_known_bad` `test_no_network_guard` `test_keystore`
`test_manual` `test_ingest_no_fetch` `test_attachments`

What they hold: a posting cannot nominate any URL it likes and have the app
follow it; URL policy is enforced on every hop of a redirect, not just the
first; an API key is not readable in `config.json`; the hosts this app refuses
to read stay refused, and a collector cannot talk it into widening that.

### Your data, and getting it back
`test_notes_and_offers` `test_export` `test_status_import`
`test_status_vocabulary` `test_rescreen_preserves_status`
`test_stable_keys_import` `test_retire` `test_retire_cli` `test_resumes`
`test_apply_url` `test_apply_kind` `test_prune`
`test_collect_stores_only_matches`

Notes append and are never replaced. A rescore never overwrites a status you
set. Removing a job hides it and keeps it. Everything exports to CSV.

The one place the app does delete is `prune`, and the pair of tests around it
is what bounds it: a posting is only ever removed if it failed your own
criteria AND you never touched it - no status you set, no note, nothing
opened - and the earlier rounds of a seat are kept whenever its latest round
survives, so a repost history is never quietly shortened.

### Duplicates and reposts
`test_dupes` `test_dupe_rebalance` `test_reposts`
`test_refresh_groups_duplicates`

Grouping is the only judgement the app makes on your behalf, so it is also the
one you can inspect and undo. A seat advertised again a month later is a new
round, not a duplicate, and both stay.

### Handoffs from other collectors
`test_collectors` `test_collector_namespace` `test_collector_schedule`
`test_collector_refetch_scope` `test_collector_contract_doc` `test_handoff_csv`
`test_handoff_formats_agree` `test_ingest` `test_import` `test_rekey`
`test_delist_verb` `test_closures_handback`

Anything this app will not read itself can arrive from a program you choose to
run. `test_collector_contract_doc` holds `COLLECTORS.md` against the code, so
the published contract cannot drift from what actually happens.

### When it runs
`test_refresh` `test_next_anchor` `test_weekend_anchor` `test_recheck_scope`
`test_run_ceiling_and_log`

### Plumbing
`test_concurrency` `test_runlock` `test_config_renames`
`test_module_entry_point` `test_progress_output` `test_version_is_stated_once`
`test_frozen_engine_lists_every_collector` `test_docs_match_reality`

Two connections to one SQLite file; one collect at a time across processes; a
renamed setting carries your old value across rather than resetting it.

The last three are a kind of their own: consistency checks on things this
project writes down twice. The version, across every file that states it.
The collector list, against the frozen engine's copy of it. And the prose -
the source table, the command reference, this index, the stated Python
floor, and every path a comment points at - against the code it describes.
A list nothing compares to its source is a list that has already drifted.

## The desktop app

The Rust half has its own tests, run separately:

```
cd desktop
cargo test
```
