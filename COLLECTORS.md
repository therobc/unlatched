# Writing a collector for Unlatched

Unlatched reads company career pages and ATS boards that publish access on
purpose. It does not read Indeed, LinkedIn, Glassdoor or Jobs4TN, and it is not
going to.

That is a position rather than a limitation, and this document is why. Anything
Unlatched will not read itself, it will accept from a program **you** choose to
run. Your collector writes a file; Unlatched reads it. The two programs share
nothing else.

You do not need to read Unlatched's source to write one. Everything it will
accept is below.

---

## The shape of the arrangement

**We pull. You never push.** Your collector writes a file and stops. Unlatched
decides when to read it. One program writes to the job database and it is
Unlatched.

This is not ceremony. It is what makes the rest possible:

* Your collector needs no access to anybody's job data. A folder it can write
  to is the whole permission set.
* Unlatched fingerprints your file and refuses to re-take an unchanged one.
  That guard only exists because the reading side owns the timing.
* Turning your collector off is deleting a line of config, not stopping a
  program.
* Everything that arrives is validated. Markers are stripped, bad rows are
  reported by row, closures that match nothing are named.

---

## The file

**CSV is the default.** Get a blank one with the columns already right:

```
unlatched ingest --template > jobs.csv
```

**JSON is also read**, and is usually easier if you are writing a program
rather than filling in a spreadsheet. Unlatched decides which one it is by
looking at the content, not the file name: a file starting with `{` or `[` is
JSON, anything else is CSV. Name it whatever you like.

### CSV

One header row, one row per job. Column names are case-insensitive and spaces
or hyphens are the same as underscores, so `Apply URL` and `apply_url` are the
same column. Columns Unlatched does not know are ignored.

| Column | Required | What it is |
|---|---|---|
| `title` | **yes** | The job title. A row without one cannot become a job. |
| `url` | yes\* | Where the posting is. |
| `key` | yes\* | Your own stable id for this posting. See *Identity* below. |
| `company` | no | The employer's name. |
| `location` | no | Free text: `Remote, US`, `Knoxville, TN`. |
| `posted` | no | When the employer posted it. |
| `description` | no | The posting's own words. Multi-paragraph is fine. |
| `apply_url` | no | Where applying happens, if that is a different address. |
| `apply_kind` | no | `external` or `easy-apply`. See below. |
| `status` | no | What the person already did: `applied`, `pass`, `denied`... |
| `applied_at` | no | When they did it. |
| `closed` | no | `TRUE` means this posting is gone. See *Closures*. |
| `generated_at` | no | When your collector wrote this file. See *Staleness*. |

\* One of `url` or `key`. With neither, there is nothing to identify the row by
and it is reported and skipped.

Excel's byte-order mark is handled. Embedded newlines, commas and quotes are
handled, as long as you write the file with a real CSV writer (Excel and
Python's `csv` module both qualify) rather than by joining strings with commas.

### JSON

```json
{
  "version": 1,
  "generated_at": "2026-08-13T08:00:00-04:00",
  "jobs": [
    {
      "title": "Support Analyst",
      "url": "https://boards.greenhouse.io/example/jobs/1",
      "company": "Example Employer",
      "location": "Remote, US",
      "posted": "2026-08-13",
      "description": "The posting's own words.",
      "apply_url": "https://boards.greenhouse.io/example/jobs/1/apply",
      "apply_kind": "external",
      "key": "example:1"
    }
  ],
  "closed": ["example:47", "example:48"]
}
```

`{"jobs": [...]}` or a bare `[...]` are both accepted. Fields are the CSV
columns above, minus `closed` and `generated_at`, which are properties of the
file rather than of a row.

**`version` is not optional in spirit even though the reader is permissive.** A
contract without one cannot change. `unlatched ingest --check` says so if it is
missing.

---

## Identity: you supply the id, we own the namespace

**Send your own stable id in `key`.** Whatever your collector calls this
posting internally is the right answer, as long as the same posting gets the
same value tomorrow.

**Unlatched puts it in your namespace and you cannot opt out.** Configured as
`linkedin`, your `998877` becomes `linkedin:998877`. Two collectors reporting
the same posting stay two rows with their own provenance instead of one
silently overwriting the other.

A prefix you supply is never trusted to name a namespace. A file arriving as
the `linkedin` collector carrying `indeed:123` becomes `linkedin:indeed:123` -
unambiguous, and unable to collide with anything of `indeed`'s. The only prefix
that is replaced rather than kept is your own id, so sending `linkedin:998877`
and `998877` mean the same row.

**Send no key and one is derived from the url**, with query strings dropped, so
the same posting reached through two tracking links is one job rather than two.
That works. Your own id is better, because it survives an employer changing
their URL scheme.

**A key that changes is a duplicate.** If your ids are not stable across runs,
every run adds a fresh copy of every posting and the person's board fills with
the same job.

---

## `apply_url` and `apply_kind`

`apply_kind` says how a posting is applied to:

* `external` - applying happens at a URL. `apply_url` should carry it.
* `easy-apply` - applying happens inside the site your collector read, and
  there is no address to send anybody to.

Say nothing and it is inferred: a row carrying a real destination is `external`
by observation. Nothing is inferred the other way, which is the point of the
field - an empty `apply_url` used to mean both "no external route exists" and
"we failed to capture one", and those are opposite facts.

**Never put a classification in `apply_url`.** A literal `easy-apply`, `closed`
or `n/a` there is reported and stripped rather than stored. It is worth saying
because the failure is silent in the worst way: those are non-empty values, so
they pass every "does this row have a destination" test - and then every
easy-apply row in the batch carries the same string and duplicate detection
folds them into one job.

Tracking parameters in `url` are fine. They are dropped when a key is derived
from it, so the same posting reached through two different tracking links is
one job. The link itself is stored as you sent it.

---

## Closures

A posting that has gone is worth more than one that never arrived. Somebody
waiting to hear back needs to know the opening closed.

* **CSV**: set `closed` to `TRUE` on the row. `true`, `yes`, `y` and `1` all
  work. Such a row is a closure and **not** a job - it is not imported as a
  live posting.
* **JSON**: a `closed` list at the top level of the object.

**Keys only, never objects.** Closure is one fact about a posting and the
identity is the entire payload for it.

Closures are matched against what is already on the person's board. Any that
match nothing are reported by key rather than swallowed - if the two sides
disagree about identity, that report is the only place it shows.

Closures are applied **after** the jobs in the same file, so a posting present
in both lists ends up closed. The closure is the later fact.

If your collector does not detect closures, say so in its config entry
(`pushes_closures: false`, the default) so nobody assumes dead postings will
disappear on their own.

---

## Staleness: stamp your file

`generated_at` - an ISO 8601 timestamp - is when **your collector** wrote the
file, not when anything was posted.

This is the field that catches your collector dying. When it stops running, its
file stops changing and still parses perfectly. Nothing in the content would
reveal that, and every mechanical check keeps passing while the data silently
ages. Unlatched reads this stamp and names your collector out loud when its
file has not changed and is more than 36 hours old.

A file without one is not an error. It just cannot be checked, and the app says
"age unknown" rather than pretending it is fresh.

---

## Writing the file: atomically, in the same directory

Unlatched may read your file at any moment, including the moment you are
writing it.

**Write a temporary file in the same directory as the destination, then
rename it over the destination.** Not "write in place quickly". Not "write to
a temp folder and move it".

The same-directory part is not incidental: rename is only atomic within a
volume. A temp file on another drive degrades to a copy, which reopens exactly
the window it was meant to close - and a half-read handoff is the worst kind of
failure, because it parses. It is just incomplete, and nothing anywhere says so.

```python
tmp = destination.with_suffix(destination.suffix + ".tmp")
tmp.write_text(payload, encoding="utf-8")
os.replace(tmp, destination)       # same directory, atomic
```

---

## Configuring it

In the person's `config.json`:

```json
"collectors": [
  {
    "id": "linkedin",
    "label": "LinkedIn collector",
    "path": "C:/Users/you/collectors/linkedin/handoff.csv",
    "enabled": true,
    "schedule": ["13:00"],
    "we_may_refetch": false,
    "pushes_closures": true
  }
]
```

Only `id` and `path` are required. The rest:

| Field | Default | Meaning |
|---|---|---|
| `label` | the id | What it is called on screen. |
| `enabled` | `true` | `false` stops both the schedule and the menu entry. |
| `schedule` | `[]` | Times of day to look, as `"HH:MM"`. Empty means every refresh. |
| `we_may_refetch` | `false` | Whether Unlatched may re-read your rows' URLs. |
| `pushes_closures` | `false` | Whether you report closures yourself. |

`id` is 1-32 characters of `a-z`, `0-9`, underscore or hyphen. It becomes both
the row namespace and the provenance shown on screen, so it cannot contain a
colon.

**Pulling on demand.** A person can pull at any time from **Collect -> From a
collector** on the Companies screen, or:

```
unlatched ingest --collector linkedin      # just this one
unlatched ingest                           # all of them
```

Asking always ignores the schedule.

---

## What Unlatched will not do, whatever your file says

Your file is untrusted input. This is the part to read before assuming
anything.

* **It is parsed, never executed.** No eval, no dynamic import, no path from
  your file used to locate code.
* **`we_may_refetch: true` cannot widen what Unlatched fetches.** It is a
  request to re-read your rows, and it is checked against the person's own
  rules afterwards. Hosts read only with a person present stay refused for a
  collector's rows no matter what; blocked aggregators stay refused for
  everybody, including the person. Naming your collector `indeed` gains you
  nothing - the host decides, and the host is still Indeed.
* **A malformed row is reported and skipped, never fatal.** One bad row in a
  run of hundreds must not cost the other hundreds.
* **There is a size ceiling** (64 MB). A runaway or hostile file is refused
  rather than read into memory.
* **URLs are validated before storage.** Anything that is not http or https
  with a real host is dropped - the row is kept and loses its link, because a
  posting whose URL we refuse is still a real job. (The stricter check, against
  addresses pointing inside the person's own network, applies where Unlatched
  would *fetch* a URL. It never fetches yours.)
* **An existing status is never overwritten.** If the person marked a job
  applied, your file cannot clear or change that. You know what you gathered;
  you do not know what has happened on their side since.
* **Fields you send that Unlatched does not store are ignored**, not rejected.
  Your schema is allowed to be richer than ours.

---

## Checking your work

```
unlatched ingest --check jobs.csv
```

Reads the file, imports nothing, and reports what is wrong **per row**, with
the row number a spreadsheet shows. It exits non-zero when there is something
to fix, so you can put it in your own build.

```
C:/example: csv, 37 job(s), 62 closure(s)
  written   2026-08-13T08:00:00-04:00
  row 14: no title, so this row cannot become a job
  row 22: apply_url says 'easy-apply', which is a classification rather than a
          destination - use apply_kind for that
  row 31: ignored column(s): compnay
```

That last one is why unknown columns are reported even though they are
harmless: a misspelled column looks exactly like an extra one.
