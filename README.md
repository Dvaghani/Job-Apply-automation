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
ingest (Greenhouse/Lever/Ashby/Adzuna) → hard filters → LLM fit score
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

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config.example.yaml config.yaml     # your watchlist and filters
cp profile.example.md  profile.md      # your background, for scoring
cp resume.example.json resume.json     # your master resume, for tailoring
cp applicant.example.yaml applicant.yaml   # your details, for filling forms
export ANTHROPIC_API_KEY=sk-ant-...    # or run `ant auth login`

pip install -e ".[browser]" && playwright install chromium   # for `apply`
```

`config.yaml`, `profile.md`, `resume.json`, `applicant.yaml` and
`applications/` are all gitignored — they're personal.

## Use

```bash
jobpipe ingest     # fetch from every configured board
jobpipe score      # score everything unscored, with Claude
jobpipe run        # both of the above — the daily command
jobpipe review     # open the review queue at localhost:5000
jobpipe tailor     # tailor your resume to everything you approved
jobpipe apply <fingerprint>   # open the form, fill it — you submit
jobpipe stats      # counts by status
```

A daily cron is the intended shape:

```
0 8 * * *  cd ~/Job-Apply-automation && .venv/bin/jobpipe run
```

Then open `jobpipe review` when you have a spare twenty minutes, approve what
looks right, and run `jobpipe tailor`.

## How it works

**Ingest.** Public ATS endpoints only — Greenhouse, Lever and Ashby all
publish free, unauthenticated JSON job feeds, one company per call. Build a
watchlist of board slugs and you get clean structured data with no scraping,
no bot detection, and no terms-of-service problem. Adzuna is available as a
broader aggregator (free tier: 1,000 calls/month) for companies not yet on
your list.

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
best score first. Approve or reject. That's the whole point of the design:
a human decision on every application, with the boring 80% already removed.

Re-ingesting never overwrites an existing row, so a rejected job stays
rejected and a score you already paid for is never recomputed.

**Tailoring.** For each job you approved, Claude selects which of your master
resume's bullets to use, reorders them, and rephrases each one toward the
posting's own vocabulary. Output lands in `applications/<company>-<title>/`:

```
resume.md        tailored resume
resume.html      same, print-to-PDF styled
cover-letter.md  ~200 words, or omit with --no-cover-letter
NOTES.md         matched keywords, honest gaps, and the verification result
```

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

**Assisted autofill.** `jobpipe apply <fingerprint>` opens the posting's
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

Application forms are also the one place the resume needs to be a PDF, so
`apply` renders `resume.html` to `resume.pdf` with the same browser before
filling.

If Playwright's bundled Chromium doesn't suit, point `JOBPIPE_CHROMIUM` at
any Chrome or Chromium binary.

## Configuration

`config.yaml` — watchlist and filters. Every filter key is optional; omit one
to skip that check.

```yaml
sources:
  greenhouse: [stripe, figma]      # token from boards.greenhouse.io/<token>
  lever:      [leverdemo]          # slug from jobs.lever.co/<slug>
  ashby:      [linear, ramp]       # slug from jobs.ashbyhq.com/<slug>

filters:
  remote_only: true
  locations: ["toronto", "remote"]
  min_salary: 150000
  max_age_days: 30
  title_exclude: [intern, director]
  title_include: [engineer, developer]

model: claude-opus-5               # scoring model
min_score: 60                      # below this never reaches the queue
```

Scoring defaults to `claude-opus-5` because score quality is the whole value
of this stage — a bad score costs you a real application slot. If you're
scoring hundreds a day and want to cut cost, set `model: claude-sonnet-5` or
`claude-haiku-4-5`.

`profile.md` — your background in plain prose. The scorer sees only this, so
specifics beat adjectives, and an honest **Gaps** section makes scores far
better calibrated than a list of strengths alone.

`resume.json` — your master resume in
[JSON Resume](https://jsonresume.org/schema/) format. Structure matters:
tailoring addresses individual `highlights` by index, which is what makes
per-bullet verification possible. Treat the master as a **superset** — put
every accomplishment you might ever want in it, and let each tailored resume
be a subset.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

145 tests, no network. Source parsers run against recorded payload shapes,
the tailoring pipeline runs end to end with the model call stubbed, and
autofill is driven by a real headless Chromium against a synthetic ATS form
covering every label shape. The job-board adapters have separately been
verified against live Greenhouse, Lever and Ashby boards.

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

All four phases are in. What's left is using it: fill in your watchlist,
filters, profile and master resume, then run it daily.
