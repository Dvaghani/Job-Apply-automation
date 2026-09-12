# Job Application Automation — Market Research (Phase 0)

**Date:** 2026-09-12
**Scope:** Personal use, single job seeker. Not a SaaS product, not multi-tenant.
**Goal of this doc:** map what already exists before writing any code, and decide
build vs. buy vs. hybrid.

---

## 1. TL;DR

- **Full auto-apply is a solved-but-losing strategy.** Dozens of tools do it. The
  data available says high-volume auto-apply converts badly (~2% or worse interview
  rate vs. 9%+ for targeted applying), and some recruiters actively tag spam
  applicants in shared ATS databases.
- **The parts worth automating are the boring ones:** job *discovery* +
  *deduplication* + *ranking*, resume/cover-letter *tailoring*, and *tracking*.
  The final "click submit" is the least valuable and the riskiest step.
- **LinkedIn is the worst place to automate.** Its User Agreement bans bots,
  headless browsers, and scraping extensions outright, and enforcement in 2026
  escalated from throttling to vendor-level takedowns. Every open-source LinkedIn
  Easy Apply bot ships with an "at your own risk / you may get banned" disclaimer.
- **The safe, high-leverage surface is ATS job feeds** (Greenhouse, Lever, Ashby,
  Workable) which are public JSON endpoints, plus aggregator APIs like Adzuna with
  a free tier.
- **Recommended shape:** a personal pipeline that ingests from ATS/aggregator APIs,
  scores fit with an LLM, generates a tailored resume + cover letter, and puts the
  application in a review queue where I approve and submit with browser autofill
  assistance. "Human-in-the-loop, one keystroke to submit" rather than "fire and
  forget."

---

## 2. The stack, in four layers

Almost every tool on the market is one or more of these. Useful for deciding what
to build and what to buy.

| Layer | What it does | Automate? |
|---|---|---|
| **1. Discovery** | Find postings, dedupe, filter by location/salary/keywords | **Yes — highest value, lowest risk** |
| **2. Matching / ranking** | Score how well a posting fits my profile; reject the noise | **Yes — LLM does this well** |
| **3. Tailoring** | Rewrite resume bullets + cover letter per posting | **Yes, with review** |
| **4. Submission** | Fill and submit the actual form | **Assist, don't fully automate** |
| **(5. Tracking)** | Status, follow-ups, recruiter contacts | Yes — cheap and useful |

---

## 3. Commercial tools

Pricing is as reported publicly in 2026; verify before subscribing, these move.

### True auto-apply (bot submits for you)

| Tool | Price | Notes |
|---|---|---|
| **LazyApply** | ~$99/yr lifetime-style deals; ~$12.99/mo plan | The volume veteran. Blasts LinkedIn/Indeed/ZipRecruiter Easy Apply with the *same* resume. No tailoring. |
| **JobCopilot** | ~$0.93–1.05/day (~$30/mo) | Runs a continuous agent: you set filters, it finds and applies. Closest to "set and forget." |
| **Sonara / Massive / JobWizard / FastApply** | $20–50/mo | Crowded me-too tier. Similar pitch, varying tailoring quality. |
| **Oaki, Jobloo, Glever, Tsenta** | $20–40/mo | Newer "quality over volume" positioning — auto-apply *plus* per-job AI tailoring. |

### Assist / autofill (you click submit)

| Tool | Price | Notes |
|---|---|---|
| **Simplify Copilot** | Free tier; Advanced ~$14.99/mo | Best free autofill extension. Explicitly *not* auto-apply — fills the form, you submit. Closest to the model I'd want. |
| **Teal** | ~90% free; premium ~$9/wk | Best free tracker. Ties resume/cover-letter tailoring to each tracked application. |
| **Huntr** | Free tier; Pro ~$40/mo ($30/mo quarterly) | Tracker + AI resume + matching. |
| **Jobright** | Freemium | Strongest matching engine reported — recommends by *skills*, not job title. Insight-heavy, automation-light. |
| **LoopCV** | Paid tiers | Notable for A/B testing resume variants across applications. Also sells a **job board API** aggregating 30+ sources (Greenhouse, Lever, Ashby, Workday, Indeed…) — relevant as a data source even if I don't use the apply feature. |
| **Jobscan** | Paid | ATS keyword-match scoring. Narrow but does one thing well. |

**Read:** if I wanted to buy rather than build, the honest answer is
**Simplify (free autofill) + Teal (free tracking)** covers 80% of the value at
$0/mo. The paid auto-apply tier mostly sells volume, which is the thing the
evidence says doesn't work.

---

## 4. Open source

### LinkedIn Easy Apply bots (Python + Selenium)

The most-starred category, and the most legally/operationally fraught.

- **`Auto_Jobs_Applier_AIHawk`** (AGPL) — the big one. LLM-driven: scrapes
  listings, generates tailored resumes/answers, applies. Original author
  (feder-cr) moved on to a commercial product; now community-maintained across
  many forks (`AIHawk-FOSS/Auto_Jobs_Applier_AI_Agent`, and others). Forks vary
  wildly in quality and freshness — pick carefully.
- **`wodsuz/EasyApplyJobsBot`** — LinkedIn + Glassdoor, auto-login, auto-answers
  additional questions.
- **`NathanDuma/LinkedIn-Easy-Apply-Bot`** / **`madingess/EasyApplyBot`** /
  **`nicolomantini/LinkedIn-Easy-Apply-Bot`** — the classic Selenium lineage;
  most of the others are forks of these.
- **`JorgeFrias/LinkedIn-GPT-EasyApplyBot`** — GPT answers the questions. Notably,
  **the author now discourages using it** and recommends a resume optimizer
  instead: more effective, and no risk of a LinkedIn ban. That's a meaningful
  signal from someone who built the thing.
- **`aminblm/linkedin-application-bot`** — also reaches Glassdoor, AngelList,
  Greenhouse.

**Common caveats across all of them:** selectors break constantly as LinkedIn
ships UI changes, so these repos rot fast — check last-commit date before
investing; and every README warns about account restriction.

### n8n workflow templates (self-hosted, no-code-ish)

A surprisingly mature ecosystem, and a good fit for "personal use, self-hosted."

- Templates exist for: LinkedIn job scrape → GPT-4o analysis vs. your skills →
  rewrite master resume → Google Docs/Gmail output.
- **Telegram-bot resume tailoring**: send a job URL, get back a resume adapted to
  it (uses JSON Resume format + OpenRouter).
- Multi-agent designs: an "Orchestrator" agent builds a JSON strategy brief from
  job + CV, then specialist agents write each document.
- Free templates on n8n.io; paid bundles on Gumroad (~treat those as
  ideas-to-steal, not purchases).

**Read:** n8n is the fastest path to a working v1 if I want it self-hosted with
minimal code. Downside: workflow-as-JSON is painful to version control and test
compared to a real repo.

---

## 5. Building blocks if I build it myself

### 5a. Job data sources (this is where the leverage is)

| Source | Cost | Notes |
|---|---|---|
| **Greenhouse public boards** | Free | `boards-api.greenhouse.io/v1/boards/{token}/jobs` — one company per call. Need a list of board tokens. |
| **Lever** | Free | `api.lever.co/v0/postings/{slug}` — same pattern, per-company. |
| **Ashby / Workable** | Free | Public posting feeds, same per-company model. |
| **Adzuna API** | Free tier: 1,000 calls/mo | Real aggregator with a genuine free tier. Includes salary data. Good default. |
| **JSearch (RapidAPI)** | Freemium | Reads Google for Jobs, so it surfaces LinkedIn/Indeed/ZipRecruiter/Glassdoor results. No webhooks, no dedup — you dedupe. |
| **JobsPipe** | Free 1,000 jobs/mo, then ~$49/mo | Normalizes 30+ ATS/board sources into one JSON schema. Saves a lot of adapter code. |
| **LoopCV Job Board API** | Paid | 30+ sources incl. LinkedIn/Indeed/Glassdoor. |
| **USAJobs** | Free | If US government roles are in scope. |

The per-company ATS endpoints are the underrated move: if I build a watchlist of
~100 target companies' board tokens, I get clean, structured, ToS-friendly,
free job data with no scraping and no bot detection. That's the backbone.

### 5b. Browser automation for the submit step

| Tool | Shape | Fit |
|---|---|---|
| **Playwright** | Deterministic scripting | Still the gold standard. Best when forms are known and stable. Playwright CLI reportedly uses ~4x fewer tokens than its MCP server for agent use. |
| **browser-use** | LLM-driven agent | Fastest to prototype; good when the form varies. |
| **Stagehand** | Hybrid (code + AI steps) | Middle ground — deterministic where you can, AI where you can't. |
| **Skyvern** | LLM + computer vision | Reportedly the best specifically at *form filling* (85.85% WebVoyager for 2.0); job applications are a stated top use case. ~90s for a 30-field form vs 12+ min manual. |

### 5c. LLM

Use the cheapest model that handles structured extraction and rewriting; reserve
a stronger model for the actual resume/cover-letter generation. Costs at personal
volume (say 200 tailored applications/month) are small — low single-digit dollars
to low tens of dollars per month.

---

## 6. Constraints and risks — read before building

1. **LinkedIn ToS.** Automated tools, bots, scrapers, headless browsers, and
   extensions are prohibited outright. 2026 enforcement included blocking 78.2M
   fake accounts and flagging 23.5M automated sessions in one quarter. Detection
   now includes behavioral signals (the "rhythm" of a script — actions at
   mathematically regular intervals), not just volume. **Conclusion: don't
   automate LinkedIn actions from my real account.**
2. **Bot protection on ATS forms.** Cloudflare Turnstile and similar challenge
   suspected bots pre-submit, scoring on JS signals, headers, and device
   validation. Headless submission at volume will hit this.
3. **Volume doesn't convert.** Huntr's own data: a targeted 11–20 application week
   → 9.25% interview rate; 100+ applications/week → 2.58%. Recruiters now see
   300+ applications per role (3x the 2021 baseline); LinkedIn takes 11,000+
   applications *per minute*. Standing out matters more than throughput.
4. **Reputational risk.** Reports that spam applicants get tagged in ATS
   databases shared across agencies — applying to irrelevant roles or the same
   role repeatedly can get you blacklisted, which is far worse than a wasted hour.
5. **Maintenance tax.** Selectors break; job boards change; ATS vendors change.
   Anything scrape-based is a permanent maintenance commitment.
6. **Source quality caveat.** Much of the comparison/pricing data above comes from
   vendor and competitor blogs with an obvious incentive to rank themselves well.
   Treat effectiveness claims as directional, and verify pricing at the source.

---

## 7. Proposed architecture for a personal build

Human-in-the-loop, review-queue-centric. Optimizes for *fewer, better*
applications rather than throughput.

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐
│  INGEST     │───▶│  SCORE       │───▶│  REVIEW QUEUE │
│             │    │              │    │               │
│ • Greenhouse│    │ LLM fit score│    │ I approve /   │
│ • Lever     │    │ vs. profile  │    │ reject, 1 key │
│ • Ashby     │    │ + hard rules │    │               │
│ • Adzuna    │    │ (salary, loc,│    └───────┬───────┘
│             │    │  visa, years)│            │
│ dedupe by   │    └──────────────┘            ▼
│ (co, title, │                        ┌───────────────┐
│  location)  │                        │   TAILOR      │
└─────────────┘                        │ resume bullets│
                                       │ + cover letter│
                                       └───────┬───────┘
                                               ▼
                                       ┌───────────────┐
                                       │  ASSISTED     │
                                       │  SUBMIT       │
                                       │ autofill via  │
                                       │ Playwright,   │
                                       │ I click send  │
                                       └───────┬───────┘
                                               ▼
                                       ┌───────────────┐
                                       │  TRACK (SQLite)│
                                       │ status, dates, │
                                       │ follow-ups     │
                                       └───────────────┘
```

**Why this shape:**
- Ingest layer uses only free, public, ToS-clean endpoints. No LinkedIn scraping.
- The review queue is the whole point — it's what keeps quality up and keeps me
  out of the "blacklisted spammer" bucket.
- Assisted submit sidesteps bot detection because a human is genuinely in the
  loop and the session is a real browser I'm driving.
- SQLite + a local web UI is enough. No infrastructure required.

**Suggested v1 slice (a weekend):** Greenhouse + Lever ingest for a hand-curated
watchlist of ~50 companies → SQLite → LLM fit score → simple local web page
listing today's matches, sorted. No tailoring, no submission yet. That alone
replaces the most tedious hour of the process.

---

## 8. Build vs. buy

| Option | Cost | Verdict |
|---|---|---|
| Buy auto-apply (JobCopilot/LazyApply) | $100–360/yr | Sells volume, which is the failing strategy. **No.** |
| Buy assist (Simplify + Teal free tiers) | $0 | Genuinely good. **Use it now, today, regardless.** |
| Fork an OSS LinkedIn bot | $0 + ban risk | **No** — ToS violation on my primary professional account. |
| n8n template | $0 self-hosted | Fast v1; awkward to version control. **Good prototype path.** |
| Custom pipeline (above) | ~$5–20/mo LLM | **Yes** — the discovery/ranking layer is genuinely unserved and low-risk. |

**Recommendation: hybrid.** Use Simplify + Teal free tiers immediately for
autofill and tracking. Build the custom ingest → score → review pipeline, because
that's the part no tool does well for a *specific* person's criteria. Skip
autonomous submission entirely.

---

## 9. Open questions for phase 1

- [ ] Which ~50 companies go on the watchlist? (Determines Greenhouse/Lever board tokens to collect.)
- [ ] Hard filters: minimum salary, locations, visa/sponsorship, remote-only, years-of-experience ceiling?
- [ ] Resume source of truth — JSON Resume format? LaTeX? Markdown → PDF?
- [ ] Where does it run — local cron, or a small always-on box?
- [ ] Is cover-letter generation actually worth it, or do most target ATSes not read them?
- [ ] Notification channel for new high-score matches: email, Telegram, or just the local UI?

---

## Sources

Market / tool comparisons:
- [Top 10 AI Automated Job Application Tools: 2026 Ranking — Valasys](https://valasys.com/top-10-ai-tools-for-automated-job-applications-tested-and-ranked/)
- [Best Auto-Apply Tools 2026 — Oaki](https://www.oaki.io/blog/best-auto-apply-tools-2026)
- [12 Best AI Job Application Automation Tools 2026 — FastApply](https://blog.fastapply.co/best-ai-job-application-automation-tools-2026)
- [9 Best AI Auto-Apply Tools in 2026 — Resumly](https://www.resumly.ai/best/best-ai-auto-apply-tools)
- [Best AI Job Search Tools in 2026: Honestly Compared — RemoteHunt](https://remotehunt.app/blog/best-ai-job-search-tools-2026)
- [Best Huntr Alternatives for Job Tracking in 2026 — Resumly](https://www.resumly.ai/alternatives/huntr-alternatives)
- [7 Best Simplify Alternatives for AI Job Search in 2026 — Lifeshack](https://www.lifeshack.com/alternatives/simplify/)

Open source:
- [GitHub topic: linkedin-easy-apply](https://github.com/topics/linkedin-easy-apply)
- [AIHawk-FOSS / Auto_Jobs_Applier_AI_Agent](https://github.com/Intusar/Auto_Jobs_Applier_AI_Agent)
- [wodsuz/EasyApplyJobsBot](https://github.com/wodsuz/EasyApplyJobsBot)
- [NathanDuma/LinkedIn-Easy-Apply-Bot](https://github.com/NathanDuma/LinkedIn-Easy-Apply-Bot)
- [JorgeFrias/LinkedIn-GPT-EasyApplyBot](https://github.com/JorgeFrias/LinkedIn-GPT-EasyApplyBot)
- [aminblm/linkedin-application-bot](https://github.com/aminblm/linkedin-application-bot)

APIs / data:
- [6 ATS Platforms with Public Job Posting APIs (2026) — Cavuno](https://cavuno.com/blog/ats-platforms-public-job-posting-apis)
- [Free Jobs API: 8 Real Options Compared (2026) — JobsPipe](https://jobspipe.dev/free-jobs-api)
- [Adzuna API — PublicAPI](https://publicapi.dev/adzuna-api)
- [Job Board API — LoopCV](https://www.loopcv.pro/job-board-api/)

Browser agents:
- [11 Best AI Browser Agents in 2026 — Firecrawl](https://www.firecrawl.dev/blog/best-browser-agents)
- [9 Browser Use Alternatives Compared for 2026 — Skyvern](https://www.skyvern.com/blog/browser-use-alternatives/)
- [Browser Tools for AI Agents Part 2: The Framework Wars — DEV](https://dev.to/stevengonsalvez/browser-tools-for-ai-agents-part-2-the-framework-wars-browser-use-stagehand-skyvern-4gn)

Risk / ToS / effectiveness:
- [Is LinkedIn Automation Safe in 2026? ToS & Scraping Rules — ConnectSafely](https://connectsafely.ai/articles/is-linkedin-automation-safe-tos-scraping-guide-2026)
- [LinkedIn Automation Rules 2026: Banned vs. Safe Tools — Northlight](https://northlight.ai/blog/is-linkedin-automation-against-the-rules)
- [Recruiters in 2026 Face 300+ Applications — Metaintro](https://www.metaintro.com/blog/recruiters-drowning-2026-300-applications-per-role-climbing)
- [Why Mass Apply Strategies Fail in 2026 — ResumeYourWay](https://www.resumeyourway.com/blogs/news/the-post-application-economy-why-mass-apply-strategies-are-failing-in-2026)
- [The AI Job Search Arms Race: Why Mass Applying Is Backfiring — oTechWorld](https://otechworld.com/the-ai-job-search-arms-race-why-mass-applying-is-backfiring-on-candidates/)
- [AI Hiring in 2026 statistics — Enhancv](https://enhancv.com/blog/ai-hiring-statistics/)
- [Protect your forms from spam and abuse — Cloudflare](https://developers.cloudflare.com/use-cases/solutions/protect-sensitive-forms-fraud-abuse/)

n8n:
- [AI-powered automated job search & application — n8n template](https://n8n.io/workflows/6391-ai-powered-automated-job-search-and-application/)
- [Automate job applications with AI resume tailoring using GPT-4o — n8n template](https://n8n.io/workflows/11215-automate-job-applications-with-ai-resume-tailoring-using-gpt-4o-linkedin-and-gmail/)
- [n8n Automated Job Application Assistant — Medium](https://medium.com/@haowg/n8n-automated-job-application-assistant-0bd9e1a00ecb)
