# Job Apply Automation

Personal job-application automation. Currently in **research phase** — no code yet.

## Status

| Phase | Status |
|---|---|
| 0 — Market research | ✅ Done — [research/01-market-landscape.md](research/01-market-landscape.md) |
| 1 — Ingest + scoring pipeline | Not started |
| 2 — Tailoring | Not started |
| 3 — Assisted submit + tracking | Not started |

## Research summary

The market is saturated with "auto-apply" bots, and the evidence says they don't
work — targeted applying converts at ~9% interview rate vs. ~2.6% for high
volume, and mass applicants risk being tagged as spam in shared ATS databases.

So the plan is **not** another auto-apply bot. It's a human-in-the-loop pipeline
that automates the tedious parts (discovery, dedup, ranking, tailoring) and keeps
a human on the submit button:

```
ingest (Greenhouse/Lever/Ashby/Adzuna) → LLM fit score → review queue
  → tailor resume + cover letter → assisted autofill → track in SQLite
```

Deliberately **out of scope**: automating LinkedIn actions. It's a direct User
Agreement violation and 2026 enforcement is aggressive.

See the [full research doc](research/01-market-landscape.md) for the tool
comparison, pricing, open-source survey, API options, and risk analysis.
