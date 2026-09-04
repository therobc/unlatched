# Changelog

What changed in each release, in the words of somebody using the app rather
than the code.

## 0.1.30 - 2026-09-02

### Added

- **Find out when an employer moves to a different job system.** When a company
  changes the software behind its careers page, nothing announces it: the app
  keeps asking the old address, gets nothing back, and that employer quietly
  stops appearing. It looks exactly like nobody hiring. **Companies -> Re-check
  for moved employers** now re-reads the careers pages of the employers you
  already have and tells you who moved, who has become readable, and who cannot
  be read any more.

  It reports and changes nothing. `unlatched rediscover --apply` is what
  updates them, and `--company` checks a single employer instead of all of
  them. It never runs on its own schedule - re-reading everybody's careers page
  is something to do when you ask, not on a timer.

  This matters most for the starter employers, which all arrive on the same day
  and therefore go out of date together.

- **Employers that move are now found again on their own.** The re-check above
  is the whole sweep, and it is still something you ask for. But an employer
  whose board has gone quiet does not need a sweep: a collection now counts the
  times each board comes back with nothing, and after three in a row it
  re-checks that one employer and updates the address. A check that finds
  nothing never clears an address that works, and only a few employers are
  re-checked in any one collection - a bad connection makes every board look
  quiet at once, and that is exactly when it must not go looking.

  This is what keeps the starter employers working instead of going stale
  together.

- **The weekend collection time is on the Config screen.** Saturday and Sunday
  run once rather than twice, later in the day, and that time was settable only
  by editing `config.json` by hand - so the screen showed the weekday times
  while the weekend used a time you could not see.

- **Take in one collector's file from the dashboard.** The dashboard already
  tells you when a collector has left something new - how old the file is, and
  whether it has been read in yet - and the only way to act on that was to go
  to the Companies page and find the menu there. That menu is now on the
  dashboard too, beside Refresh. Refresh still takes in everything at once;
  this is for when you can see that one of several has delivered. It appears
  only if you have a collector set up.

- **Two search settings that were doing something you could not see.** "Only
  jobs I can work from the United States" was **on** and filtering - a remote
  posting in another country is still in another country - with nothing on
  screen saying so. Alongside it, "include employers who send people out to job
  sites", for work based near you but done at customer sites. Both are now tick
  boxes under the places you can work.

### Fixed

- **`unlatched recheck` works again.** Run without `--json` it stopped with an
  error before printing anything, on every run - it was reading a result the
  command had stopped producing. Nothing was wrong with the check itself; the
  report on the end of it could not finish.
- **The engine reports the version it actually is.** It had been answering
  `0.1.1` to `--version`, and introducing itself to every employer's server
  under that name, since that was the current release. Both now say what is
  really running.
- **Jobs in Washington, D.C. can be searched for.** The District was being read
  as Washington state, which broke it both ways at once: postings in the
  District answered a search for Washington state - the other side of the
  country - and a search for "DC" matched none of its own city. Towns named
  after another state were wrong the same way, so Oregon OH, Indiana PA,
  Delaware OH and Nevada MO all answered the wrong search.
- **Re-importing your history no longer doubles it.** Importing the same
  exported file twice appended every entry again. That is not untidiness: the
  funnel, the Applied column and the response rate are all counted from that
  history, so it doubled the number of applications you appear to have made,
  and nothing on screen looked wrong. Importing a file you already have now
  changes nothing and says so.
- **"Checked N added links" counts the links it checked.** It was counting only
  the ones found to have closed, so the ordinary result - every link read, every
  one still open - reported as "checked 0".
- **A federal search says when it could not read everything.** USAJOBS is a
  national search rather than one employer's board, and a query with more
  matches than one run can read stopped quietly. It now names the query and
  what it did not reach, and suggests narrowing the titles or places.
- **Boards that stop short say so.** Three of them cut off without a word, so a
  large board looked like a small one.
- **A posting whose title matches loosely is ranked like one that matches
  exactly.** Searching for "help desk" found "IT HelpDesk Analyst" and then
  scored it twenty points lower than an exact match - so the postings the
  looser matching exists to find sank to the bottom of the list.
- **Keywords measures against the resume you actually attached.** It was
  reading a setting that attaching a resume never fills in, so for anybody who
  set the app up the way it describes, there was no resume to measure at all -
  every tracked skill was listed as a gap and the covered list was always
  empty. That is also what a genuinely poor resume looks like, so there was
  nothing on screen to tell the two apart.
- **And it says so when it cannot read your resume.** A PDF or a Word file
  still cannot be opened by the window itself. Rather than reporting that as a
  resume matching nothing, it now names the file and says what will read it -
  the command line handles Word, and a plain-text copy works everywhere.
- **The resume marked "in use" is the one being read.** Pinning a copy took
  effect for scoring but not for the marker on the Resumes screen, so the app
  could point at one document while measuring against another.
- **The Config screen can always be saved.** Turning weekend collection off and
  clearing the weekend time left Save reporting a problem about the daily
  refresh - and the box it was asking you to fill in was greyed out, because
  the run it belongs to was switched off. There was no way out of it from
  inside the app.
- **The Pipeline explains the statuses this app actually has.** Hovering
  "Heard back" named one status that no longer exists and left out four that
  do.
- **A column cannot appear twice.** A saved column layout naming the same
  column twice drew it twice, with two rows in the column settings that
  fought each other.
- **The last thing the engine says is not lost.** A run that finished at the
  same moment it printed its final line could drop that line - which is
  usually the one that says what happened, including why adding a job failed.
- **A settings file that will not open is kept.** If `desktop_settings.json`
  was damaged - a power cut mid-save, a hand edit - the app fell back to its
  defaults and then wrote them over the only copy, taking the column layout,
  theme and browser choice with it. The damaged file is now set aside instead.
- **Tidying up after adding a job by link.** The posting text you pasted was
  left in a file in your profile folder afterwards.

### Changed

- **The button that re-reads your added links works while a collection is
  running.** It used to grey itself out and wait for you to come back. It now
  queues like everything else you press, and says what it is waiting for. The
  separation itself is unchanged and deliberate: added links are never re-read
  on a schedule, only when you ask - see the README for why that distinction
  is what makes reading them defensible at all.

- **A job you add while a collection is running is now queued, not dropped.**
  Only one engine command runs at a time, and a scheduled collection can hold
  that for half an hour. Adding a job by link during one used to close the
  dialog, add nothing, and say nothing - the reason went to a log shown on
  another screen. It now waits its turn and runs the moment the collection
  finishes, and the strip along the bottom says what it is waiting for.
  Pressing Add twice queues it once; two different jobs both survive.

- **Every command now says what it DID, not just that it finished.** "Taking
  in a handoff finished" is not an answer to "did it take anything in". The
  engine's own last word is carried to the bottom strip, so a run that found
  nothing new says so instead of looking identical to a button that does
  nothing.

- **Adding a job by link now recognises the employer's job board.** The link
  people actually have - the "original job post" an aggregator sends you to -
  names the board in its address. Reading it means one paste registers that
  employer for good: everything they post from then on arrives on its own,
  from their own board. A link that names no board records none, as before.

- **robots.txt is now read the way every crawler reads it.** Rules were being
  matched in file order, so a site whose file says "Allow: /" and then lists
  exceptions had every exception ignored - and a site that says "Disallow: /"
  and then permits its careers page was refused outright. The most specific
  rule now wins, which is both more careful about what a site closes and able
  to read pages a site deliberately opened.

- **"Near misses" is now two cards: "Below salary" and "Requirements not
  aligned".** One card held four unrelated things - pay under your floor, an
  employment type you did not ask for, a requirement your profile rules out,
  and a description too thin to judge - under a name that described none of
  them. "They do not pay enough" and "this is not my job" are different
  answers and worth acting on differently, so they are now different cards.

  Your existing jobs are sorted into the two automatically when you next open
  the app, whichever way you open it - the app and the collector both do it,
  and it happens once. Where a job was held back on pay, the app can tell exactly and puts
  it under Below salary; where it cannot tell, the job goes under Requirements
  not aligned rather than being guessed at, and re-screening sorts it. Nothing
  is hidden either way, and no job is in neither.

- **A link attached to a job has to be a web address.** Anything else is
  refused when you attach it, with a reason, rather than stored and quietly
  ignored later.
- **One search setting that never did anything has been removed.** `remote_scope`
  accepted a fourth value that read like the opposite of "remote only" and in
  fact filtered nothing at all. Ticking only "On-site" under ways of working is
  what that setting was for, and it works.

- **One site is no longer on the never-read list.** Reading it is now decided
  the same way as for any other site: its own robots.txt. Nothing else about
  the list has changed, and the aggregators that were never read are still
  never read.


## 0.1.29 - 2026-08-26

### Fixed

- **A posting you added by hand is handed back too, if it came from the same
  board.** The hand-back went by the label saying which collector delivered a
  row, so a job you pasted in yourself - from the very same board - was left
  out of it. It now goes by where the posting lives, so the sender hears about
  a closure whichever way the job reached your list. A posting from somewhere
  that sender has never seen is still none of its business.


## 0.1.28 - 2026-08-26

### Added

- **The dashboard says when your collector's file was last read in, not only
  how old it is.** Those are different questions - one is whether the collector
  ran, the other is whether this app has the results - and only the second one
  is fixed by pressing Refresh. The line already worked out the answer in order
  to warn you when a file was waiting; it just never said it out loud.

- **Postings you mark taken down are handed back to the collector that sent
  them.** Until now the flow ran one way: your collector told this app what had
  closed, and anything you discovered yourself stayed here. So it kept those
  leads on its own board and kept spending its rate-limited budget re-checking
  them. A file now goes back the other way, rewritten at the end of every
  refresh, or on demand with `unlatched closures`. What travels is "closed",
  never "removed" - taking a row off your own lists is your business, not the
  sender's.

- **Mark one posting taken down, from the row you are on.** An advert that
  closes between two collections still reads as open until the next run finds
  it gone, and the only way to say so was to tick boxes and use the bulk
  button. It is now in the status dropdown and on the `x` key - for the job you
  open, find closed, and want out of the list without waiting for the next
  collection. Anything you had already recorded about it is kept.

### Fixed

- **Column headings sort when you click on the word, not just beside it.**
  Clicking a heading's text selected the text instead of sorting - headings
  were selectable, the way body text is - so the only part that worked was the
  empty space to its right. On a narrow column there is barely any of that,
  which is why sorting looked broken on most columns and fine on one or two.
- **The note prompt opens with the cursor in the box.** It deliberately did
  not, so that Enter could save without a multiline field turning it into a new
  line. Both work now: Enter saves whether or not you are typing, and
  Shift+Enter starts a new line.

- **The dashboard no longer says "of N collected".** That number was the size
  of your list, which used to be the same thing as how many postings had been
  read - it is not any more, now that a collection keeps only what matches. It
  now says how many of the jobs you are holding match your search, and leaves
  "how many were read" to the collection's own summary, which is where it is
  actually known.
- **The run log records what each employer's board yielded.** It named every
  employer before reading them - which is what tells you where a slow run is -
  and then never wrote down what came back. It now does, including how many
  postings were read but not kept, so a board that offers 900 and keeps 3 reads
  as working rather than broken.


## 0.1.27 - 2026-08-26

### Added

- **Choose which browser job links open in.** Links open in whichever browser
  this device already uses, as before - that stays the default. A job hunt
  often lives in a different browser from the rest of your day, though, with
  its own logins and autofill, so Settings can now send postings to one you
  pick. If that browser is later moved or uninstalled, links go back to the
  device default rather than failing.
- **A one-off clean-up for databases collected before this release.** Those
  hold every posting ever read, matching or not. `unlatched prune` (a
  command-line tool, run once) deletes the ones that failed your criteria and
  that you never opened, judged or wrote a note about - reporting how many that
  is and taking a backup before it does. Anything you touched, anything you
  removed by hand, and the earlier rounds of a job still in your lists are all
  kept. Nothing collected from this release on needs it.

### Changed

- **Collections no longer keep every posting they read.** Boards are still read
  in full and every posting is still screened; what does not match your search
  is simply not written down. A month of collecting had produced 14,479 stored
  jobs to carry the 978 that matched, and every collection was walking all of
  them - which is what made the app slow to respond while one was running.
  Nothing is lost: a posting still on a board is read and screened again on the
  next run, so changing your criteria still applies to everything out there.


## 0.1.26 - 2026-08-26

### Added

- **Choose which resume is screened against.** The newest optimised copy used
  to win automatically, so attaching anything newer silently took over and an
  older one could not be chosen at all. The choice is recorded where the engine
  reads it, so the "in use" marker cannot disagree with the document actually
  being screened.

### Fixed

- **A status change is never lost to a busy database.** Setting a status while
  a collection was running could fail with "database is locked", discarding the
  status and any note typed with it. The change is now held and applied the
  moment the collection finishes, and says so.
- **Collections stop blocking the app for so long.** The pass that annotates
  reposts held the database for its whole run - every row in one transaction -
  which is what produced that error. It now lets go periodically. The work and
  the result are unchanged.


## 0.1.25 - 2026-08-26

### Added

- **Easy Apply is shown.** A collector can mark whether applying happens on
  the site it read or somewhere else, and the app has been storing that and
  never showing it. "Found at" now says *Easy Apply* where that is what it
  is, and the host otherwise.
- **A note prompt you can switch off, per status.** Settings has a tick box for
  each status; unticked, that status is recorded straight away with no prompt.
  Applied ships unticked, since it is the one set most often and least often
  written about. Offer always asks - that prompt is where pay and the offer
  date are captured, and they exist nowhere else.

### Changed

- **Triage is what you have not decided on.** Applying moves a job to the
  Pipeline; Pass takes it out; a posting pulled before you touched it goes too.
  All three were already true of some statuses and not others - now it is one
  rule. Nothing is lost: everything is still in All jobs.

## 0.1.24

Everything from 0.1.6 to 0.1.24 shipped in one working session, and the
per-patch split was not recorded as it happened. Rather than invent one after
the fact, it is grouped here. Releases from this point on get their own entry,
and the release tool refuses to build without one.

### Added

- **Search, from every jobs screen.** Finds by title, employer, location,
  description, requirements, notes, URL and source. It searches everything you
  have, including postings that were taken down or removed - the other lists
  exist to narrow, this one exists to find. Statuses are deliberately not
  searched; filter for those instead.
- **Where your matches come from** now splits into *your collectors* and *job
  boards*, with the age of each source beside it. A collector that has missed
  its usual window is marked. Built-in boards are never marked - they run when
  the app runs, so "late" is not a thing they can be.
- **The handoff file reports itself** at the top of the dashboard: whether it
  is there, how old it is, when the app will next look at it, and whether a new
  one has arrived that has not been taken in yet.
- **Refresh now takes in whatever a collector has left for you**, so a
  collection that finishes early no longer waits for the next scheduled run. It
  says so when it cannot - during a collection, for instance.
- **A durable record of every collection.** One timestamped file per run under
  `logs/` in your search folder, naming each employer before it is read. A run
  that stalls now names the employer holding it while it is still stalled.
- **Columns** moved to a button in the toolbar, and the jobs table scrolls
  sideways when the columns are wider than the window.

### Changed

- **The status breakdown counts decisions you made.** Postings that expired
  without you ever touching them are no longer counted as though they were -
  they were 805 of 863 on one real search, which made the ring almost entirely
  a colour meaning "nobody looked at this".
- `closed` now reads **Expired**: the employer pulled it, you never touched it.
- **Triage drops postings that expired and were never acted on.** An
  application whose posting came down still shows - that is news, not clutter.
  "Show finished jobs" brings the rest back.
- **The Pipeline is what is still in play**: jobs you applied to, and only
  until they are settled. A rejection, a declined offer or a closed posting
  takes one out. The count of finished applications is stated rather than left
  to be noticed.
- **A collect cannot run for ever.** Four hours for a whole run, ten minutes for
  any single employer, both configurable. A run cut short says how many
  employers it did not reach and does not record itself as complete, so the
  schedule still knows the work is owed.
- **Freshness lines say which collector they are about.** "Collected today" now
  reads "Boards collected today", because the built-in sweep runs whenever the
  app runs and was answering on behalf of collectors that had not run at all.

### Fixed

- **A single employer could hold a collection for hours.** One Oracle tenant
  was fetching a description for every one of ~500 postings to keep the seven
  that matched the search - 99% of that work was thrown away on a job title
  already known. Titles are now filtered before the request. That employer went
  from ten minutes to under thirty seconds.
- **Descriptions already collected are never overwritten with nothing.** A
  posting returned without its body no longer blanks the text stored for it.
- **A status set from the search results now shows immediately.** The change was
  always saved; the list was not being re-read, so the row went on showing the
  old value.
- **The app now wakes for its own schedule.** Left open, it consulted the
  schedule once at launch and never again, so a daily collection could be
  missed for as long as the window stayed open.
- **The dashboard's source ages update while the app is open**, rather than
  being frozen at whatever they were when it started.
- Typing in the jobs list is much faster on a large search - the list no longer
  loads the full text of every posting to show a preview of it.
