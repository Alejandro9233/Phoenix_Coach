# Phoenix Coach

Personal AI triathlon/marathon coach. FastAPI backend on Render + SwiftUI iOS app.
Scrapes a COROS watch, generates periodized weekly plans with an LLM.

Design: **Python = GPS, LLM = Coach.** Python does the math that must be exact
(periodization, volume ceilings, compliance). The LLM picks workouts inside
those limits and writes the coaching notes.

## Rules

- Short, plain words. Lead with the answer. A 3-item list beats three paragraphs.
- **`.env` `DATABASE_URL` is the production Render Postgres. Never test against it.**
  `load_dotenv(override=True)` in main.py overrides any `DATABASE_URL` passed on
  the command line — neutralize `dotenv.load_dotenv` first, then assert the URL
  is sqlite before touching anything.
- Dates go through `get_local_today()`, never `date.today()`. Servers run in UTC,
  which flips the date at 5pm Hermosillo time.
- Don't set `TIMEZONE` in `.env` or on Render. It overrides the phone's reported
  timezone and breaks travel.
- Commits go straight to `main` on this repo.
- **Tell Alex what changed before committing.** Summarize the edits in chat
  first; commit only after he's seen it. A commit is never the first report
  of work.
- **Never add a `Co-Authored-By:` trailer to commits.** No Claude/AI attribution
  lines, no `Generated with` footers. Commit messages end at the body.
- The LLM never decides volume. Wrong mileage in a plan = bug in
  `periodization_engine.py` or the context injection, not the prompt.
- **A constraint that lives only in a prompt is a suggestion.** Sport availability
  and injuries are enforced in `constraint_enforcer.py`, which strips violating
  workouts *after* generation. Every path that writes `plan_json` must run
  `enforce_constraints` before persisting. Never "fix" a violated constraint by
  rewording the prompt.

## Don't re-add these — removed on purpose

- **On-device MLX chat** (`LocalLLMManager.swift`), deleted 2026-07-27. Chat is
  backend-only SSE. There is no offline path, by choice.
- **Journal tab** (`Views/Dashboard/DashboardView.swift`, `FeedbackEntry`,
  `NetworkManager.submitFeedback`), deleted 2026-08-16. RPE/motivation/soreness
  sliders that were write-only — no endpoint ever read them back, no screen ever
  showed one, and `sleep_quality` was posted but never even stored. Their sole
  consumer was three lines of LLM context in `data_agent`. Chat + `issue_triage`
  covers the same ground and actually changes the plan. The `POST /feedback`
  endpoint and `athlete_feedback` table are intentionally still there — historical
  rows predate the deletion and `data_agent` still reads them. Nothing writes new
  ones.
- **Local notifications** (`NotificationManager.swift`, Profile > Notifications
  toggles), deleted 2026-08-16. Two of the four fired on a 5-second timer from
  `TodayView` right after a refresh *you* triggered — a banner about work you were
  already watching. The app has no push infrastructure and no notification
  permission prompt, by choice. Anything worth telling the athlete belongs in the
  UI they already have open.
- **`_fallback_weekly_plan`** (`response_agent.py`), deleted 2026-08-18. A
  rule-based template week that shipped as the athlete's real plan whenever the
  LLM failed (2026-08-17: retired Groq model → silent "Base Building" template
  persisted). Failed generation raises; plan endpoints return 502 and persist
  nothing. `test_weekly_plan_safety.py` guards it.
- **`frontend/`** web UI — replaced by the iOS app.
- **Root `phoenix_coach.db`** — no SQLite snapshot in the repo. Use `scripts/rebuild_db.py`.

## Commands

```bash
# Backend (local dev, port 8001)
PYTHONPATH=. ./venv/bin/python3 backend/main.py

# iOS build — simulators here are iPhone 17 series, not 15
cd ios/PhoenixCoach && xcodebuild build -project PhoenixCoach.xcodeproj \
  -scheme PhoenixCoach -destination 'platform=iOS Simulator,name=iPhone 17 Pro'

# COROS scraper health check
PYTHONPATH=. ./venv/bin/python3 scripts/scraper_health_check.py
```

## Where things live

- `backend/main.py` — all endpoints. `grep -n '^@app\.' backend/main.py` to list them.
- `backend/models/database.py` — all tables. `grep -n '__tablename__'` to list them.
- `backend/services/periodization_engine.py` — phases, volume targets. Docstring has
  the ceilings and why the distance profiles exist.
- `backend/services/constraint_enforcer.py` — the hard gate. Strips workouts that
  violate availability or an active injury. Read its docstring before touching
  anything that generates a plan.
- `backend/services/issue_triage.py` — "my calf is shot" → plan change. Detect in
  chat, propose, apply only on confirm. Docstring has the three-step shape.
- `backend/agents/` — `data_agent` summarizes state, `response_agent` holds prompts
- `ios/PhoenixCoach/` — 4 tabs: Today, Coach, Recent, Profile.
  **Recent** = `Views/Feedback/FeedbackView.swift` — the one tab whose name
  doesn't match its filename.
- `docs/DEPLOY.md` — Render env vars, free-tier limits, rollback. Read before deploying.

## Gotchas

- **Render cold start**: free tier sleeps after 15 min; first request takes 30-60s.
  Expected — iOS handles it with a 180s timeout. Don't "fix" it.
- **COROS scraping fails** → check auth first, not the parser. Login breaks on
  auto-redirects. Run `scripts/scraper_health_check.py`.
- **No migration framework** — `main.py` does `ALTER TABLE` on startup in
  `_ensure_columns()`. New columns must be nullable.
- **Plan day keys are full English names** (`"Sunday"`), from `strftime("%A")`. iOS
  must use a `en_US_POSIX` `"EEEE"` formatter — `shortWeekdaySymbols` yields `"sun"`
  and silently misses, and a Spanish phone yields `"domingo"`.
- **uvicorn buffers logs** when started non-interactively. Missing output ≠ didn't run.
- Tests use in-memory SQLite with a `get_db` override, so they're safe to run on a
  machine holding production credentials: `PYTHONPATH=. ./venv/bin/pytest backend/tests/`
