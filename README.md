<img src="desktop/assets/icon.png" alt="Unlatched" width="128" align="right">

# Unlatched

A local-first desktop app that finds jobs on company career pages and ATS
boards, screens them against your resume, and tracks your application
pipeline. Everything runs on your machine, everything is stored in a local
SQLite database, and nothing about how a job is scored depends on anyone's
server or model.

Most openings are published on a company's own careers site well before they
reach a job board, and some never reach one at all. Unlatched works that
layer directly: give it company names, and it finds their careers pages,
identifies the applicant tracking system behind them, and pulls the postings
from the same public JSON feeds those systems publish for exactly this
purpose.

## What it looks like

The screens below come from a demo profile. Every employer, posting, salary
and application in them is invented - the companies are the standard fiction
(Contoso, Fabrikam, Northwind) on `.example` domains that cannot resolve. The
counts and proportions are the shape a two-month search actually takes,
because a screenshot full of threes and fours shows nothing about what the
tool is for.

**Dashboard.** What the pile is made of, and what each number excludes. Every
card opens the list it counts, built from the same query that produced the
figure - so clicking a 223 lands on 223 rows.

![The Unlatched dashboard: counts by state, a status donut, the application
funnel, the skills most asked for that a resume does not evidence, where
matches came from, and employer board coverage](docs/screenshots/dashboard.png)

**Triage.** One row per posting, sorted by score, with the reason a job fell
short shown on the row rather than hidden behind it. `pay` means the salary
came in under your floor; `fit` means something other than the money. The
`manual` rows are links added by hand, and **Check added links** is the only
thing that re-reads them - never a schedule, for the reason given above.

![The Unlatched triage list: postings with title, employer, location, posted
age, salary range, score, resume fit percentage, source board and match
verdict](docs/screenshots/triage.png)

**Keywords.** What the market is asking for, measured across the postings
worth having, split into what your resume evidences and what it does not.
No model is involved: it is token matching against a vocabulary you control.

![The Unlatched keywords report: skills demanded but not evidenced by the
resume, and skills demanded and covered, each with a demand count and
percentage](docs/screenshots/keywords.png)

**Pipeline.** Every application still in flight, with its history. Notes and
transitions are kept append-only, so the record of what happened does not
change when a status does.

![The Unlatched pipeline: applications grouped by employer with their status
badges, dates and notes](docs/screenshots/pipeline.png)

## Design principles

**Local-first, no account, no telemetry.** Your search config, your resume,
your pipeline history: all of it lives in a directory you own. Delete the
directory and the app has never heard of you.

**A link you added by hand is re-read only when you ask.** The scheduled
collection handles what the app ships with: employer job boards and search
sources that publish access on purpose. Anything you pasted in yourself sits
outside that, behind its own **Check added links** button, and never runs on a
timer.

That separation is the whole reason the feature is defensible. A link you add
might point anywhere, including a site whose robots.txt asks automated tools
to stay off its job pages. Reading the one page you are looking at, so you can
record the job you are about to apply for, is not crawling - but it stops
being true the moment a schedule does it while you sleep. Keeping the two
apart is what lets the app be useful about a page without quietly becoming a
crawler on it. Each added link is re-read at most once a day, and if a
collection has run since they were last checked, the app says so rather than
leaving them silently stale.

**Deterministic scoring, always.** Screening, ranking, and keyword coverage
are regex, vocabularies, and token math. No model can move a score. Two
people with the same data get the same answers, and the tool works exactly
the same for someone who cannot or will not pay for an AI subscription.

**Optional AI, bring your own endpoint.** One config block takes an
OpenAI-compatible base URL and an optional key. Point it at a local Ollama
instance or a hosted endpoint; both are the same code path. The model is
used only for judgment calls where reproducibility does not matter, such as
drafting search terms for an unfamiliar field. It is never used for scoring,
and scraped posting text is never sent to it.

**One command surface, two front ends.** The CLI and the desktop app expose
the same operations over the same database. Anything you can click, you can
script.

## Install

### Windows, the easy way

Run **`Unlatched-Setup-<version>.exe`**. It installs for the current user
only, needs no administrator rights, and puts the app in
`%LOCALAPPDATA%\Programs\Unlatched` with a Start Menu entry. Re-running a
newer installer upgrades in place and leaves your data alone.

There is also a **portable zip** (`Unlatched-<version>-portable-win64.zip`) if
you would rather not install anything: unzip it and run `Unlatched.exe` from
wherever you put it.

Both bundle the engine, so Python is not required.

**Windows will warn you, and it is right to.** The installer is not
code-signed, so SmartScreen shows "Windows protected your PC" - click **More
info**, then **Run anyway**. This is not a formality to wave away: that warning
is the same one a genuinely malicious download gets, so verify the file instead
of trusting the click.

Every release publishes a SHA-256 for each file. Check it before you run
anything:

```powershell
Get-FileHash .\Unlatched-Setup-<version>.exe -Algorithm SHA256
```

If the hash does not match the one on the release page, do not run it.

A code-signing certificate would not remove the warning either - Windows
SmartScreen clears a new binary on accumulated reputation, not on the presence
of a signature - so the hash is the check that actually tells you something.

### From source

Requires Python 3.11+ for the engine and CLI, and Rust for the desktop app.

```
pip install .
unlatched init
cargo build --release        # in desktop/, for the GUI
```

`unlatched init` creates the data directory (set `UNLATCHED_HOME` to put it
somewhere specific) with a default `config.json` you can edit directly, via
`unlatched config set`, or in the desktop app's Config view.

To build the installer and portable zip yourself: `python
packaging/build_release.py`.

## Where your data lives

A **profile is just a folder**. It holds `config.json`, `unlatched.db`, an
`attachments/` directory, and whatever resume you point it at. Nothing else
anywhere on your machine matters.

The desktop app organises profiles as `Documents/Unlatched/<Person>/<Search>/`
so one person can run several searches, but that is a convention, not a
requirement - any folder works. The CLI takes `--home <path>` or reads
`UNLATCHED_HOME`.

**Pointing the app at data you already have:** use "New profile", browse to the
existing folder, and Create. An existing `config.json` is never overwritten and
your database is opened in place with any schema migrations applied. A folder
that does not exist yet gets created with a default config and a fresh
database - the same code path either way.

Removing a profile from the dropdown only forgets it. The folder and everything
in it are left alone.

## Quick start

```
unlatched config set search.salary_floor 70000
unlatched config set search.work_modes '["remote","hybrid"]'
unlatched discover --file companies.txt
unlatched collect
unlatched screen
unlatched jobs
```

`collect` reports each board as it reads it. "Not kept" is the postings that
did not match your search - they are read and screened, and simply not
written down, so a board that offers hundreds and keeps three reads as
working rather than broken. A board too big for one run says so, and the
rest of it is read over the runs that follow:

```
Northwind Systems                [greenhouse] 34 collected, 6 qualified, 28 not kept
Fabrikam Retail                  [workday] 600 collected, 18 qualified, 582 not kept  (stopped at this source's 600-posting ceiling; the rest is read over the next few runs)
Contoso Health                   [ashby] 12 collected, 3 qualified, 9 not kept
```

`jobs` is the result - highest score first, with the pay range where the
employer published one and nothing where they did not. Example values, real
format:

```
[ 82.0] greenhouse:1042              Technical Support Analyst                    Northwind Systems    $68,000-$81,000
[ 74.5] workday:2251                 IT Service Desk Specialist                   Fabrikam Retail      $65,000-$72,000
[ 71.0] ashby:88                     Application Support Engineer                 Contoso Health       $70,000-$90,000
[ 66.5] recruitee:4471               Desktop Support Technician                   Tailspin Logistics
[ 61.0] myboard:998877               Service Desk Analyst (Tier 2)                Adventure Works      $58,000-$64,000
```

The key on each row is what the rest of the commands take: `unlatched show
greenhouse:1042` for the full record, `unlatched status set greenhouse:1042
applied` once you have.

Or in the desktop app: add employers on the **Companies** page (or press **Add
starter employers** for a list of national employers with boards this app can
read), then **Collect -> Every employer**.

## What it does

### Finding work

- **Careers-page discovery.** Give it a company name; it finds the careers
  page, identifies the ATS behind it, and remembers.
- **Employers who move are found again, on their own.** An employer that
  changes applicant tracking system does not announce it: the old address
  simply stops returning postings and they go quiet, which looks exactly
  like an employer who is not hiring. So a collect counts the times each
  board comes back empty, and after three in a row it re-checks that one
  employer and updates the address. A check that finds nothing never
  clears an address that works, and at most a few employers are re-checked
  in any one collect - a bad connection makes every board look quiet at
  once, and that is precisely when this must not go looking.

  This is what keeps the starter employers working rather than slowly
  going stale.
- **Or ask for the whole sweep.** `unlatched rediscover` re-checks every
  employer you have and reports who moved, who became readable, and who
  cannot be read any more. It reports by default and only writes with
  `--apply`, and it is never scheduled - re-reading fifty careers pages is
  a thing to do when you ask, not on a timer.
- **Ten readers for the major applicant tracking systems**, plus schema.org
  JSON-LD and sitemap enumeration for employers running something bespoke,
  and three sources that need no employer list at all. All fifteen are
  listed below.
- **Federal jobs** through USAJOBS - see its own section below.
- **A daily refresh** that decides for itself whether collecting is worth
  doing, rather than polling. Two anchor times a day by default, one at the
  weekend, and a gap of a whole day or more collects immediately instead of
  waiting for the next anchor.
- **Add a job by link** for anything you found yourself. Paste the URL and it
  is tracked with everything else. Whether the app *reads* that link is a
  setting that ships **off** - see "What it deliberately does not do".
- **Re-check added links** on demand, to find out which of the jobs you added
  by hand have been taken down. Never on a timer, never during a collect.

### Deciding what is worth your time

- **Screening against your resume**, deterministically: title rules,
  seniority, employment type, salary floor with a second "close enough" tier,
  work mode, and location.
- **Keyword coverage** - what a posting asks for that your resume does not
  evidence, so you can see what to add.
- **A requirements summary** compressed to a few words ("5+ yrs, BS, CDL") so
  you can rule a row out without opening it.
- **Remote evidence**, stored as the phrase that convinced it, not a guess.
- **Duplicate grouping** for one job reached two ways - the same application
  page found on two boards folds into one row, and you can inspect and undo
  every grouping.
- **Repost detection.** Employers mint a new posting id when they
  re-advertise, so identity is a *seat*: one company, one title, one place. A
  seat advertised again within a week is the employer refreshing a listing; a
  gap of more than four weeks is a new opening, kept as its own entry and
  linked back to the round it followed.

### Tracking what you did

- **A status pipeline** - Applied, Interviewed, Offer, Accepted Offer, Hired,
  Offer Withdrawn, Declined Offer, No Offer, Pass - with an optional note on
  every transition and a full history that a re-screen never touches.
- **A timeline** per job, showing every change and what you wrote about it.
- **A dashboard** that summarises and routes: what is new, what is waiting on a
  reply, what went quiet, what was taken down.
- **A copy of the posting kept at the moment you apply**, because a delisted
  posting cannot be read back off the web later.
- **Attachments** per job: the description PDF, a recruiter's email, your
  tailored resume. Files are download-only - the app renders none of them, so
  it carries no parser for any attachment format. Your own files are offered to
  an AI assistant if you use one; employer-written files never are.
- **Export** of your whole pipeline to CSV at any time.
- **Retire and restore** for rows you want out of the way without losing them.

### Working the list

Triage is keyboard-driven: **arrow keys** (or **j/k**) to move, **o** to open a
row and close it again, **a** applied, **p** pass, **i** interviewed, **d** no
offer, **n** to write a note. The row title is a link out to the employer's own
site, so **o** is what reads a posting inside the app. Every control has an
accessible name, so a screen reader announces the list, the dashboard meters
and the menus.

A first-run walkthrough points at the real controls rather than describing
them. It runs once, is skippable at any point, and can be replayed from
Settings whenever you want it back.

## What it deliberately does not do

Unlatched does not touch LinkedIn. Not scraped, not automated, not driven
through a logged-in session. LinkedIn's user agreement prohibits automated
access, and a tool that works around platform rules is not a tool you should
trust with your job search. Every source Unlatched reads publishes
machine-readable job data on purpose:

| Source | Access |
|---|---|
| Greenhouse | public JSON API |
| Lever | public JSON API |
| Ashby | public JSON API |
| SmartRecruiters | public JSON API |
| Workable | public JSON API |
| Recruitee | public JSON API |
| Workday | public JSON endpoints per tenant |
| Oracle HCM (Fusion Cloud Recruiting) | public JSON endpoints per tenant |
| BambooHR | public JSON API |
| Breezy | public JSON API |
| schema.org JobPosting | JSON-LD embedded by employers for search indexing |
| Sitemaps | standard sitemap.xml enumeration |
| Remote OK | public JSON API |
| NoDesk | published sitemap of job pages, each carrying schema.org markup |
| USAJOBS | the US government's own documented public API (free key) |

Page fetching (careers-page discovery, schema.org extraction, sitemaps)
respects robots.txt, since that is crawling and robots.txt is a crawler
directive. The documented board APIs above are accessed directly: they exist
for programmatic access, and some API hosts robots-disallow everything
purely to keep search engines out. Everything rate-limits per host,
identifies itself with an honest User-Agent, and caps response sizes.

Some sites are never read at all, whatever you do - the aggregators whose terms
forbid automated access. You can still add a job from one by link: the link is
kept, you type what you know, and nothing is requested from the site.

`fetch.read_added_links` ships **off**. Turning it on lets the app read a page
you add by hand, one job at a time, with you present. It is never used during a
collect or the scheduled refresh.

**Anything Unlatched will not read itself, it will accept from a program you
choose to run.** A collector you write - or somebody else's - exports a CSV or
JSON file, and Unlatched reads it. Your rows keep their own provenance, and
Unlatched still makes no request to whatever site they came from. The interface
is published in [COLLECTORS.md](COLLECTORS.md); `unlatched ingest --template`
gives you a blank one and `unlatched ingest --check` tells you what is wrong
with yours, row by row. Collectors appear in the desktop app under **Collect ->
From a collector**, each with its own schedule and its own manual pull.

## Federal jobs (USAJOBS)

Every other source in Unlatched reads one employer's public job board.
USAJOBS is different: it is a national search across roughly 450 federal
agencies, run by the Office of Personnel Management, and it needs a free API
key.

```
unlatched config set credentials.usajobs.email you@example.com
unlatched config set credentials.usajobs.api_key YOUR_KEY
```

Both fields are required, and the email must be the address you registered
with, because USAJOBS wants it back as the `User-Agent` on every request. In
the desktop app the same two fields live under Config -> "Job sources that
need a key". Without them, USAJOBS is skipped with one line saying so; it is
never an error.

**Register your own key.** The USAJOBS API Terms of Service scope the data to
the entity named on the registration form, and forbid sharing a key. That is
why Unlatched ships with the credential fields empty and no key of its own,
stores yours in your profile's `config.json` (which is gitignored and never
part of a release), and keeps every posting it retrieves in your local
database. Getting a key takes a minute at
[developer.usajobs.gov](https://developer.usajobs.gov), and the key is yours,
not this project's.

Search volume is bounded on purpose. The API matches a keyword as one phrase
rather than an OR of terms, so each `search.title_include` entry and each
`search.locations` entry becomes its own query; the collector caps that at 5
keywords, 5 locations, 12 combined query streams, and 5 pages each. If OPM
throttles the key anyway, the run stops with a message naming those two
settings instead of quietly returning nothing.

## Command reference

Every command takes `--home <path>` and most take `--json`.

| Command | What it does |
|---|---|
| `init` | Create a profile folder with a default config |
| `config set/get` | Read and write settings |
| `criteria` | Show what the current search actually asks for |
| `discover` | Find a company's careers page and identify its ATS |
| `rediscover` | Re-check stored employers for a changed ATS (reports first; `--apply` to update) |
| `starter --add` | Add a list of national employers with readable boards |
| `collect` | Read every configured board |
| `refresh` | Collect, but only if the schedule says it is worth it |
| `screen` | Re-score everything against the current config and resume |
| `jobs` / `show` | List and inspect postings |
| `add` | Record a job you found yourself, by link |
| `recheck` | Re-read added links to find the ones taken down |
| `status set/list` | Record and review what you did about a job |
| `dedupe` | Find and group postings that are the same job |
| `reposts` | Seats that have been advertised more than once |
| `keywords` / `coverage` | What postings ask for that your resume does not show |
| `requirements` | The compressed requirements line for a posting |
| `resume` | Manage the resume a profile screens against |
| `attach` / `attachments` / `detach` | Files kept alongside a job |
| `attach-trust` | Move one attachment between employer-written and your own |
| `ingest` | Take in a collector's handoff file |
| `collectors` | List configured collectors and their state |
| `closures` | Hand back what this app knows is closed, for the collector that sent it |
| `import` | One-off import of rows from a file |
| `export` | Write the whole pipeline to CSV |
| `retire` / `delist` | Put rows aside, or mark them gone |
| `prune` | Delete postings that never matched and nobody ever touched (reports first; `--apply` to act) |
| `forget-company` | Remove an employer record nothing points at |
| `ats-audit` | Deep parse-failure audit of a resume .docx |
| `brief` | A structured summary for an AI assistant, if you use one |
| `agent` | Check the optional AI endpoint is reachable |

## A few of the sharp edges this codebase learned the hard way

These are baked into the code and pinned by regression tests, because each
one originally shipped as a bug that produced plausible-looking wrong output:

- **Regexes that run over fetched pages are timed against 200KB of adversarial
  HTML in the test suite.** An overlapping-alternation pattern once took 1.5
  seconds over 20KB of input and 109 seconds over 160KB - eight times the
  input, seventy times the time. Character classes are kept disjoint so no
  input can backtrack.
- **Salary floors are judged against the top of a posted range.** Comparing
  against the bottom silently drops roles that pay well above your floor.
- **A posting that never mentions location is not remote.** Remote status
  requires positive evidence, and the evidence string is stored so you can
  see what convinced the screen.
- **"401(k) up to $5,000" is not a salary.** Numbers only count as
  compensation when their surrounding context says so.
- **Keyword coverage scores against a skill vocabulary, not distinct words.**
  Word-share metrics fall as postings get wordier, which punishes exactly
  the postings with the most information in them.
- **Word-boundary matching honors inflections.** A resume that says
  "Communications" evidences a posting that asks for "Communication".
- **Boilerplate cannot identify a company.** Phrase matching that resolves
  anonymized postings discards any phrase appearing in more than three
  documents, because equal-opportunity boilerplate once merged eight
  unrelated companies into one.
- **The same title in two cities is two seats, not a repost.** Grouping on
  company and title alone once reported one employer re-advertising a driver
  role 18 times; it was 18 terminals in different cities.
- **Two postings sharing an apply page are one job, unless they are more than
  four weeks apart** - then they are two rounds of hiring for the same seat,
  and folding the newer one away would hide a live opening behind a dead one.
- **Every regex gate has a self-test proving it rejects known-bad input.** A
  gate that can fail open is a gate that will.
- **Jobs are keyed by a stable source:id, never a list position**, so saved
  statuses cannot silently attach to the wrong job when new postings arrive.

## Layout

```
unlatched/      Python engine and CLI
desktop/        Rust + egui desktop app
packaging/      Release build: frozen engine, portable zip, Windows installer
tests/          pytest suite, including the regression tests above
.github/        the workflow that builds a release
CHANGELOG.md    what changed in each release, in a user's words
COLLECTORS.md   the published handoff contract, for writing your own collector
BUILDING.md     developer setup, and how the Windows release is packaged
```

Every claim on this page is checkable, and none of the checking touches the
network: `pytest` runs the whole suite in under a minute.
[tests/README.md](tests/README.md) is an index of what they cover and why each
area exists - most of them are there because something was wrong once.

## License

MIT. See LICENSE.
