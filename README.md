# Job Apply Automation

A personal job-application pipeline. Ingests postings from public ATS feeds,
scores them against your profile with Claude, and puts them in a review queue
where **you** approve and submit.

It deliberately does **not** auto-apply. See
[the research](research/01-market-landscape.md) for why: targeted applying
converts at ~9% interview rate vs ~2.6% for high-volume, recruiters see 300+
applications per role, and mass applicants risk being tagged as spam in shared
ATS databases. The bottleneck worth automating is *finding and judging* the
right roles, not clicking submit.

```
ingest (Bundesagentur/Arbeitnow/GermanTechJobs/ATS feeds/Adzuna)
    → hard filters → LLM fit score
    → review queue (you decide) → tailor resume + cover letter
    → assisted autofill (you submit) → track
```

## Status

| Phase | Status |
|---|---|
| 0 — Market research | ✅ [research/01-market-landscape.md](research/01-market-landscape.md) |
| 1 — Ingest + filter + score + review | ✅ |
| 2 — Resume/cover-letter tailoring | ✅ |
| 3 — Assisted autofill on submit | ✅ |
| 4 — Dashboard: run the whole pipeline from one page | ✅ |
| 5 — Chrome extension: fill forms in your own browser | ✅ |
| 6 — German output, and a folder you can upload from by hand | ✅ |
| 7 — German-market sources: Bundesagentur, Arbeitnow, GermanTechJobs | ✅ |

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config.example.yaml config.yaml     # your watchlist and filters
cp profile.example.md  profile.md      # your background, for scoring
cp resume.example.json resume.json     # your master resume, for tailoring
cp applicant.example.yaml applicant.yaml   # your details, for filling forms

pip install -e ".[browser]" && playwright install chromium   # for `apply`
```

**Then pick a model backend** — this decides how scoring and tailoring reach
Claude, and they bill differently:

| `backend:` | Needs | Use when |
|---|---|---|
| `claude-cli` | Claude Code installed and signed in | You have **Claude Pro or Max**. Runs on the subscription. |
| `api` | `ANTHROPIC_API_KEY` + API credits | You have API billing set up. |

A Claude Pro subscription does **not** include API access — those are
separate products, billed separately. If Pro is what you have, use
`backend: claude-cli`; it shells out to `claude -p` in headless mode and
needs no key at all. It's slower (a fresh process per call, roughly 8
seconds a job) and it consumes your Pro usage allowance, so watch the
volume on a large watchlist.

`config.yaml`, `profile.md`, `resume.json`, `applicant.yaml` and
`applications/` are all gitignored — they're personal.

## Use

```bash
jobpipe dashboard --open   # run the whole thing from a local web page
```

That's the one command worth remembering — every command below is a button
on it. The CLI is still the real interface, and the dashboard shells out to
it, so both stay in step:

```bash
jobpipe ingest     # fetch from every configured board
jobpipe score      # score everything unscored, with Claude
jobpipe score <fingerprint>   # (re)score one job, keeping its status
jobpipe run        # both of the above — the daily command
jobpipe review     # open the review queue at localhost:5000
jobpipe tailor     # tailor your resume to everything you approved
jobpipe apply      # list approved jobs and their fingerprints
jobpipe apply <fingerprint>   # open the form, fill it — you submit
jobpipe stats      # counts by status
jobpipe doctor     # check config, files, backend and sources
jobpipe export     # bundle your setup for another machine
```

If `ingest` returns less than you expect, run **`jobpipe doctor`** first. A
misconfigured source produces no jobs *and* no error, which looks exactly like
"nothing new today" — doctor makes the difference visible, including the
common YAML slip of putting `adzuna:` at the top level instead of under
`sources:`, or leaving the whole block commented out.

`--probe` runs every configured Adzuna query for real and reports the count
each one returns, which is what makes queries tunable:

```
Live check
  +   python                       31 job(s)
  !   HiL Testautomatisierung      0 job(s)  — too narrow, try fewer words
```

Adzuna requires **every word** in a query to match, so a long compound phrase
matches almost nothing. Keep queries to one or two words and let the fit
scorer do the filtering — that's what it's for. If a city returns nothing, add
`distance: 50` to search a wider radius around it.

A daily cron is the intended shape:

```
0 8 * * *  cd ~/Job-Apply-automation && .venv/bin/jobpipe run
```

Then open the dashboard when you have a spare twenty minutes, approve what
looks right, and hit Tailor.

## The dashboard

`jobpipe dashboard` serves two pages on localhost: the command deck at `/`
and the review queue at `/review`.

The deck has a button per command, the options each one takes (score limit,
`--no-cover-letter`, `--strict`, `--probe`), counts by status across the top,
and a console that streams the command's output live rather than making you
wonder whether a nine-minute scoring run has hung. Underneath, every approved
job with its own **Score**, **Tailor** and **Open & fill form** buttons, so
applying to one doesn't mean copying a fingerprint out of a list first — plus
its fit score and the scorer's reasoning, links that open each tailored
document in a tab, and the folder they were written to.

**Score** there takes a fingerprint, which rescores that one job *in place*.
That exists because a job you approved before it was ever scored otherwise had
no way to get one: `score` alone only looks at jobs with status `new`. Scoring
one by name deliberately leaves its status alone, so an approval survives it.

**Settings.** `/settings` edits the tunable half of `config.yaml` — search
terms, filters, radius, score cut-off — without opening the file. Setup stays
in the file: backends, paths and API credentials are not editable from a page,
and a credential is never rendered into one.

Each Bundesagentur term has a **Probe** button giving a live count, which is
the point of the page. Tuning a query blind means running an ingest and a
scoring pass to find out it was wrong; probing costs nothing and answers in a
second. A term returning thousands is not a search, it is the whole board.

The file is hand-maintained and its comments are half its documentation, so
edits round-trip through ruamel rather than a plain dump, and the previous
version is kept as `config.yaml.bak`. An edit is rendered, loaded to check it
still parses, and only then swapped in — writing a config that cannot be read
would break the command that could fix it. A change takes effect when the
dashboard restarts, and the page says so.

It runs commands. It does not make decisions — approving a job is still a
click you make on the review page, and **Open & fill form** still stops at
the filled form and hands you the browser.

Three things about it are worth knowing:

**It shells out to the CLI.** Every button spawns `jobpipe <command>` as a
subprocess and streams its output back. Nothing is reimplemented for the web,
so the page and the terminal cannot drift apart, and anything you can fix in
the CLI is fixed in both.

**One command at a time.** Two `ingest` runs racing each other write the same
rows, and a `score` started under a `tailor` spends model usage on work the
other is about to redo. A second command is refused while one is running, with
a Stop button if you want the slot back.

**`apply` stays answerable.** It fills the form and then blocks, holding the
browser open while you work through it — from a terminal you press Enter when
you're done. The dashboard keeps a pipe to that process, so **Done — close the
browser** is that same Enter. Nothing about the no-submit guarantee changes:
it is the same subprocess, running the same code, that a terminal would start.

What the page can spawn is an allowlist in [`tasks.py`](src/jobpipe/tasks.py),
not arguments assembled from the request — job identifiers must match the
16-hex-character fingerprint shape, numeric options must be numbers in range,
and flags are translated from known keys rather than passed through. The JSON
endpoints also require a same-origin JSON body, so another tab you have open
can't quietly start a run.

## The Chrome extension

`jobpipe apply` drives a browser this program launched, and that browser is a
fresh profile every time. It has none of your logins, none of your saved
passwords and none of your SSO sessions — which is a problem precisely where
it matters, since most employer portals want you signed in before they will
show you a form. Playwright cannot fix this: attaching to your everyday
Chrome profile is not something Chrome allows.

So there is a second front end. The extension runs in **your own Chrome**,
where you are already signed in to everything. Click its toolbar button on
any application form and a small panel appears with a **Fill this page**
button, a picker for which approved job's resume to attach, and the same
per-field report the CLI prints.

The dashboard shows the folder to load it from and the token to paste into
it. In Chrome: `chrome://extensions` → Developer mode → **Load unpacked** →
that folder → **Details → Extension options** → paste the token → **Test
connection**.

**It does not reimplement any of the matching.** The panel reads the fields
on the page and asks the local server what belongs in them; the server
answers using the same `plan_field` that `jobpipe apply` uses. Field matching
is where an autofiller is right or wrong, and a JavaScript reimplementation
of it would agree with the Python one right up until somebody edited one of
them. The extraction script is literally the same file, loaded by Playwright
in one path and as a content script in the other.

What that buys, concretely: the credential refusal, the self-identification
opt-out, the CV-parse ordering and the answer bank all work identically in
both places, and fixing one fixes both.

Three things about its reach:

**It cannot read pages you merely visit.** The manifest asks for `activeTab`,
not `<all_urls>`, and declares no always-on content script — so it has access
to a tab only after you click its button on that tab. A test asserts this,
because it is a one-word edit to loosen and nobody would notice.

**Its network access is the local server and nothing else.** `host_permissions`
covers `127.0.0.1:5000` only. All fetching happens in the service worker, not
the content script — under MV3 a content script's fetch is subject to the
*page's* CORS policy, so a call to localhost from inside an employer's
careers page would simply be blocked.

**It refuses credentials on its own.** The server never puts a password field
in a plan, and the content script would decline to write one anyway. A plan
arriving over HTTP is exactly the sort of thing that should not be trusted
just because we asked for it.

The token lives in `.jobpipe-token` (gitignored) and persists across
restarts, so you paste it once. Treat it as a password: it is what lets
something ask the server for your details.

## How it works

**Ingest.** Public ATS endpoints only — Greenhouse, Lever, Ashby and
SmartRecruiters all publish free, unauthenticated JSON job feeds, one company
per call. Build a watchlist of board slugs and you get clean structured data
with no scraping, no bot detection, and no terms-of-service problem. Adzuna is
available as a broader aggregator (free tier: 1,000 calls/month) for companies
not yet on your list.

**The German sources.** The free ATS feeds are US-tech-weighted, so a German
search needs different inputs:

- **Bundesagentur für Arbeit** — the federal register, and the primary source
  for Germany. Free, no registration, the largest by a wide margin, and it
  links to the employer rather than to itself. It can exclude Zeitarbeit and
  private recruiters *at the source* (`exclude_staffing`), which removes most
  of the noise in a German technical search: the same role relisted by six
  agencies, none of whom are the employer.
- **Arbeitnow** — open, German-focused, full descriptions in the listing. A
  general board rather than a technical one, so narrow it with `keywords` and
  `location_contains` or it contributes exactly the noise it was added to
  replace.
- **GermanTechJobs** — small, all IT, and it usually states a salary, which
  German postings almost never do. It publishes no location, so a `locations:`
  filter would reject all of it.

Two things about the federal register are worth knowing. Search is **v6**;
`/pc/v4/jobs` answers 403 and is gone, whatever the documentation says, while
job *details* are still on v4 and address a posting by its Base64-encoded
reference number. And search results carry no description, so a description
costs one extra request per job.

That second point shapes the adapter. The pipeline already refuses to spend an
LLM call on a job the hard filters would reject; `arbeitsagentur.fetch` takes
the same predicate and applies it one stage earlier, so a title you would
throw away never costs a round trip either. On a real run that skipped 205 of
323 detail requests — every job it kept had a description, and every job it
skipped was one the filters rejected anyway.

**A coverage caveat worth knowing before you build a watchlist.** The free ATS
feeds are heavily US-tech-weighted. Greenhouse, Lever and Ashby returned
nothing at all for a sample of German automotive employers (Bosch, Continental,
ZF, Vector, dSPACE, Elektrobit, IAV, Porsche, CARIAD); SmartRecruiters has the
best European reach of the four but is still thin. Employers in those markets
mostly run SAP SuccessFactors, Workday, Softgarden or their own portals, none
of which publish an open feed. If you're job-hunting outside US tech, **Adzuna
with the right `country` is your primary source**, not the watchlist.

**Dedup.** Jobs are keyed by a fingerprint of (company, title), normalized —
legal suffixes dropped, trailing `(Bengaluru, India)` / `- Remote` / `[L5]`
noise stripped. The same role posted to five offices, or listed on two
boards, collapses to one decision. On a real run across three boards that
takes 196 postings down to 161.

**Hard filters.** Deterministic rules — title terms, location, remote-only,
salary floor, posting age — run *before* any LLM call, because every job they
reject is money saved. On that same run they removed a further 121, leaving
41 worth scoring. Salary is only judged when the posting states one; most
don't, and rejecting on a missing field would empty the pipeline.

**Scoring.** Claude reads your profile and the posting and returns a 0–100 fit
score, a concrete one-line justification, and flags for things you should know
before applying ("requires security clearance", "on-site 3 days/week"). The
prompt tells it to be strict — a generous scorer wastes your limited
application budget, which is the exact failure mode the research warns about.

**Review.** A local Flask page, bound to localhost, listing what survived,
best score first. Approve or reject.

A scored job below `min_score` is not rejected — nobody has looked at it. It
has its own **Below <n>** tab, and the dashboard counts it separately, because
the count and the page it links to must agree: reporting every scored job as
"to review" is how the dashboard ends up promising 240 and the queue showing
none. If that tab is full of reasonable roles, `min_score` is set too high.

**A trap worth knowing about, if you are job-hunting in a language you are
still learning.** A `profile.md` that says a posting "written entirely in
German with fluency implied" is a poor fit will be applied literally — and
since nearly every posting in Germany is written in German, it rejects the
entire market. It cost 238 of 240 jobs here, with an average score of 6. State
the language level you have and let the scorer weigh *stated* requirements;
`score.py` now tells it explicitly that the language a posting is written in
is not a requirement. Rescoring three of the affected postings moved them from
25 to 68, 62 and 55. That's the whole point of the design:
a human decision on every application, with the boring 80% already removed.

Re-ingesting never overwrites an existing row, so a rejected job stays
rejected and a score you already paid for is never recomputed.

**Tailoring.** For each job you approved, Claude selects which of your master
resume's bullets to use, reorders them, and rephrases each one toward the
posting's own vocabulary. Projects, education (including your thesis) and
languages are copied from the master verbatim rather than rewritten — which
is why they need no verification pass, and why they are safe to include in
full. Output lands in `applications/<company>-<title>/`:

```
resume.md        tailored resume
resume.html      same, print-to-PDF styled
cover-letter.md  ~200 words, or omit with --no-cover-letter
NOTES.md         matched keywords, honest gaps, and the verification result
```

**Applying in German.** `--language de`, or the picker on the dashboard,
writes the resume and cover letter in German — headings included, so it reads
as a *Lebenslauf* rather than a translated CV.

That needs a German master (`resume.de.json` beside `resume.json`, or
`resume_paths:` in the config), and the reason is worth stating: projects,
your thesis and your languages are copied from the master rather than
rewritten by the model, so tailoring German output from an English master
produces a half-English document. A missing German master is an error rather
than a silent fallback.

Both languages live side by side in one folder — `resume.md` and
`resume.de.md`, `resume.pdf` and `resume.de.pdf` — so generating one never
overwrites the other.

**One honest caveat about German.** Verification is weaker outside English,
and the reason is grammatical rather than fixable. The vocabulary check works
by treating a mid-sentence capital letter as a proper noun; German capitalises
every noun, so that rule marks *Visualisierung* and *Testergebnissen* as
invented technologies and buries the real findings under dozens of false ones.
In German the check falls back to shape alone — `AWS`, `ECU-TEST`,
`PostgreSQL`, `S3`, and the technology half of a compound like
`HiL-Testergebnissen`. A fabricated technology whose name looks like an
ordinary noun (`Kubernetes`) would not be caught. The number check, which
catches the inflated-metric case that actually matters, is unchanged, and
`NOTES.de.md` says all of this so it is never a silent downgrade.

**Two pages.** A tailored resume is laid out to fit two, and the count is
checked rather than hoped for: the page total is read out of the PDF after
rendering, and anything longer says so in the log. The single biggest cause
was a layout bug — the print stylesheet set a `@page` margin *and* body
padding, spending 1.8in of every 11in page on nothing. Fixing that alone took
three-page resumes to two with identical content. Headings now stay with what
follows them, and bullets do not split across a page.

The model also chooses which projects earn their space, by index into the
master. That is selection, not rewriting — the text stays verbatim, so it
cannot introduce a claim — and it drops the project whose substance already
appears in a role bullet.

**Verification — the part that matters.** An LLM rewriting your resume can
quietly inflate a metric or add a technology you never used. That is the worst
failure mode here: a false claim on a document you will be interviewed
against, and you may not notice before someone else does.

So nothing generated is trusted. The model must cite the index of the master
bullet each rewrite came from, and every rewrite is checked back against it:

- **Numbers.** Any figure in a rewrite must already exist in its source
  bullet. Magnitudes are normalized first, so `2,000,000` → `2M` is
  recognized as the same claim, while `2,000,000` → `20M` is caught.
- **Vocabulary.** Any technology or proper noun named must appear somewhere
  in your master resume. Capitalization alone can't tell `Kubernetes` from
  `Built`, so position decides: mid-sentence capitals are proper nouns, and
  a sentence's first word counts only if its shape is independently
  distinctive (`AWS`, `PostgreSQL`, `S3`).
- **Indices.** A cited bullet that doesn't exist is caught.
- **Skills.** Every listed skill must be in the master.

Anything unverifiable is written to `NOTES.md` under a header telling you to
check it before sending, and logged as a warning. `--strict` exits non-zero if
anything is flagged, so a cron can refuse to proceed silently.

The model is also asked for `gaps` — what the posting wants that your resume
genuinely can't support. That's deliberate: knowing you don't qualify is
worth more than a document that papers over it.

**Assisted autofill.** Run `jobpipe apply` with no arguments to see what's
approved and ready, each with the exact command to run. Then
`jobpipe apply <fingerprint>` opens the posting's
application form in a real browser, fills every field it can from
`applicant.yaml`, attaches your tailored resume as a PDF, and then stops and
hands you the keyboard.

```
12 of 16 fields filled:

 + First Name *                      Ada
 + Email Address                     ada@example.com
 + Are you legally authorized to...  Yes
 + Will you now or in the future...  No
 + Resume/CV                         resume.pdf
 - Female                            self-identification — yours to answer
 - Protected Veteran Status          self-identification — yours to answer
```

**It cannot submit, by construction.** The element query selects only
`input`, `select` and `textarea`; buttons are excluded from it, so there is
no code path that reaches a Submit button. Filling never presses Enter
either, so a single-field form can't submit by accident. Two tests assert
this empirically against a real browser: the fixture form records every
click and every submit attempt, and both must come back empty.

**It never types a credential.** Plenty of employers — most of the German
ones — put "create an account" in front of the form. A `type="password"`
field is refused before any value is looked up, so there is no path from a
form field to your answer bank for one, and there is no opt-in to change
that. Signing up is yours to do. Password fields are still *detected* and
reported, because "0 of 0 fields filled" is a mystery and "this page wants
an account" isn't.

**Filling happens on demand, not just on load.** A single pass at page load
only works when the form is the first thing you see. Often it isn't: a
login, a cookie wall, a registration step or a multi-page wizard sits in
front of it. So the browser stays open and you can ask for another pass at
any point — `fill` in the terminal, **Fill this page** on the dashboard —
once you have got yourself to the real form. Every pass runs the same
`fill_page`, so none of them can submit either.

**Portals that parse your CV get to go first.** Many employer sites offer to
read an uploaded CV and prefill the form from it, and that parse is
asynchronous. Attaching the CV last — the obvious order, since the upload
field usually sits at the bottom of the form — means the parser's guesses
land on top of everything you just typed. So attachments are uploaded
*first*, the parse is given a moment to run, and the form is then re-read
before your own values go over the top of it.

Your `applicant.yaml` wins those disagreements: you wrote it, the parser
inferred its version. But a replacement is reported rather than done
silently, because a portal quietly deciding your phone number is something
you want to see:

```
 + Phone                             +49 170 1234567  (replaced '+49 000 1111')
```

**Aggregator links are followed to the employer.** Adzuna's API returns a
link to an adzuna.de listing rather than to the employer, so opening it
lands you on Adzuna with nothing to fill. The employer link on that page
carries a short-lived signed token, so it can't be derived offline — it is
read from the page and navigated to, still by URL, never by clicking. Adzuna
serves that hop a bot check that a headless browser fails, so this works as
`apply` normally runs but not under `--headless`. Where it lands may be
another aggregator (XING, StepStone) rather than the employer's own form —
which is exactly what filling on demand is for.

Voluntary self-identification — gender, race, veteran and disability status
— is **detected so it can be deliberately left blank**. Those questions are
yours to answer or decline, and a script shouldn't guess them. Set
`fill_self_identification: true` in `applicant.yaml` if you'd rather they
were filled from your own stated values.

Field matching handles the label shapes real ATS forms use: `label[for]`,
a wrapping `<label>`, `aria-label`, a `<fieldset>` legend, a bare text node
in a wrapper div, and placeholder-only inputs. Anything it can't identify is
reported as unmatched rather than guessed at, so you know exactly what's
still blank.

Free-text questions come from an answer bank in `applicant.yaml`, keyed by
substring, longest match wins:

```yaml
answers:
  "authorized to work": "Yes"
  "require sponsorship": "No"
  "why do you want to work": >
    I have spent six years on payments infrastructure...
```

**The folder is the fallback.** Autofill will sometimes miss — a portal with
a drag-and-drop upload it cannot reach, a wizard step it cannot follow. So
every tailored job gets a complete, ready-to-upload folder: the PDF is
rendered at tailor time rather than waiting for a form to ask for one, and the
dashboard shows each job's **absolute** folder path with an **Open folder**
button next to it. Paste the path into a file dialog, or click through to it,
and upload by hand.

If Playwright's bundled Chromium doesn't suit, point `JOBPIPE_CHROMIUM` at
any Chrome or Chromium binary.

## Configuration

`config.yaml` — watchlist and filters. Every filter key is optional; omit one
to skip that check.

```yaml
sources:
  greenhouse:      [stripe, figma]   # token from boards.greenhouse.io/<token>
  lever:           [leverdemo]      # slug from jobs.lever.co/<slug>
  ashby:           [linear, ramp]   # slug from jobs.ashbyhq.com/<slug>
  smartrecruiters: [ContinentalAG]  # slug from careers.smartrecruiters.com/<slug>

filters:
  remote_only: true
  locations: ["toronto", "remote"]
  min_salary: 150000
  max_age_days: 30
  title_exclude: [intern, director]
  title_include: [engineer, developer]

backend: claude-cli                # or `api`
cli_model: sonnet                  # CLI aliases: opus / sonnet / haiku
min_score: 60                      # below this never reaches the queue
```

Scoring defaults to `claude-opus-5` on the `api` backend because score
quality is the whole value of this stage — a bad score costs you a real
application slot. To cut cost at volume, set `model: claude-sonnet-5` or
`claude-haiku-4-5`. On `claude-cli` the model is `cli_model`, which takes
the CLI's aliases (`opus`, `sonnet`, `haiku`) rather than full model ids.

`profile.md` — your background in plain prose. The scorer sees only this, so
specifics beat adjectives, and an honest **Gaps** section makes scores far
better calibrated than a list of strengths alone.

`resume.json` — your master resume in
[JSON Resume](https://jsonresume.org/schema/) format. Structure matters:
tailoring addresses individual `highlights` by index, which is what makes
per-bullet verification possible. Treat the master as a **superset** — put
every accomplishment you might ever want in it, and let each tailored resume
be a subset.

## Moving to another machine

The code is in git. Nothing that makes it *yours* is — config, profile,
master resumes, applicant answers, the job database and everything already
tailored are gitignored on purpose, because a public repo is the wrong place
for an API key and a job search.

```bash
jobpipe export                     # -> jobpipe-setup.zip
```

Then on the other machine:

```bash
git clone https://github.com/Dvaghani/Job-Apply-automation
cd Job-Apply-automation
unzip /path/to/jobpipe-setup.zip   # over the top of the clone
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[browser]" && playwright install chromium
jobpipe doctor
```

`export` exists rather than "copy these files" because of one trap. SQLite in
WAL mode keeps recent writes in a `-wal` file beside the database, and that
file is routinely *larger than the database itself* — 6.9MB against 4MB here.
Copy `jobs.db` alone and you lose every decision since the last checkpoint.
`export` checkpoints first, so the archive holds one complete file.

The extension token is deliberately left out: a secret is better regenerated
than carried around, and the extension needs re-pointing at the new machine
anyway. Everything else comes across, including both language masters.

**Paths in the database are stored with forward slashes** whatever the
platform. A Windows path is not a path on Linux — `applicationscme` is one
filename with a backslash in it — so storing one would quietly turn every
tailored job back into an untailored one on the other machine. Rows written
before this are read leniently, so an older database still works.

What does *not* travel: Claude Code must be installed and signed in on the
new machine (or `ANTHROPIC_API_KEY` set, for the `api` backend), and
Playwright needs its own Chromium there.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

452 tests, no network. Source parsers run against recorded payload shapes,
the tailoring pipeline runs end to end with the model call stubbed, and
autofill is driven by a real headless Chromium against a synthetic ATS form
covering every label shape. The job-board adapters have separately been
verified against live Greenhouse, Lever and Ashby boards.

The dashboard's runner is tested against real subprocesses — a stand-in child
that prints a prompt and then blocks on stdin, exactly as `apply` does, which
is what pins down the two properties that are easy to get quietly wrong: that
a prompt with no trailing newline still reaches the page, and that the Done
button still reaches the process.

If the bundled Chromium build doesn't match your Playwright version, the
browser tests skip rather than fail; set `JOBPIPE_CHROMIUM` to run them.

## Not doing, on purpose

- **No LinkedIn automation.** Its User Agreement bans bots, headless browsers
  and scraping extensions outright, and 2026 enforcement escalated to
  vendor-level takedowns. Not worth your primary professional account.
- **No autonomous submission.** Bot protection on ATS forms is real, and the
  conversion data says volume is the losing strategy anyway. `apply` fills
  the form in a browser you are looking at; you read it and click Submit.

- **No fabrication.** Tailoring may only select and rephrase what your master
  resume already says. Everything generated is verified back against it.
- **No guessing at self-identification.** Detected, and left blank unless you
  opt in.
- **No typing credentials.** Password fields are refused outright, with no
  opt-in. If a portal wants an account, you make it.

- **No deciding for you.** The dashboard automates running the pipeline. It
  does not approve a job, and it has no button that submits one.

Every phase is in. What's left is using it: fill in your watchlist, filters,
profile and master resume, then open the dashboard daily.
