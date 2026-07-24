# 🏃‍♂️ Phoenix Coach — Complete Project Handoff

> **Last Updated**: 2026-07-23
> **Author**: Alex (athlete/developer)
> **Status**: MVP live on Render (cloud). Backend at `https://phoenix-coach.onrender.com`, using Groq Llama-3.3-70B. Actively iterating on premium features and UI polish.

---

## 1. What Is This Project?

**Phoenix Coach** is a personal AI-powered triathlon/marathon coaching app. It connects to a COROS watch (via web scraping), analyzes training data, and generates periodized weekly training plans using a local LLM (Ollama + Qwen3:8b).

### Architecture: Hybrid "Python = GPS, LLM = Coach"

The system uses a **two-brain design**:
- **Python (deterministic)**: Periodization math, phase detection, volume ceilings, 3:1 build/recovery cycles, compliance scoring — things that must be 100% correct.
- **LLM (creative)**: Workout selection within constraints, sequencing, coaching notes, motivational language, weekly reviews, daily adaptations — things that benefit from judgment.

The Python periodization engine computes a `TrainingContext` dict, which gets injected into every LLM prompt as guardrails.

---

## 2. Tech Stack

| Layer | Tech | Details |
|---|---|---|
| **iOS App** | SwiftUI (native) | Xcode project at `ios/PhoenixCoach/` |
| **On-device LLM** | MLX + Llama-3.2-1B-Instruct-4bit | Runs on iPhone for chat, downloads from HuggingFace |
| **Backend** | Python FastAPI | Single file `backend/main.py` (946 lines), runs on port **8001** locally or cloud via Render |
| **Database** | SQLite (local) / PostgreSQL (cloud) | `phoenix_coach.db` locally (~140MB); managed PostgreSQL on Render for production |
| **LLM (backend)** | Groq API + `llama-3.3-70b-versatile` | Cloud-hosted via `llm_client.py`; falls back to local Ollama + `qwen3:8b` when `GROQ_API_KEY` is unset |
| **RAG** | ChromaDB + markdown knowledge base | `knowledge/` dir (10 files: periodization, HR zones, workout types, etc.) |
| **Data Source** | COROS watch → Playwright scraper | Scrapes activities, EvoLab metrics, recovery data |

### How to Run (Cloud — Primary)

The backend is deployed to **Render** at `https://phoenix-coach.onrender.com`. The iOS app points there by default — just build and run from Xcode.

```bash
# Xcode: Open ios/PhoenixCoach/PhoenixCoach.xcodeproj
# Build and run on device/simulator — no local backend needed
```

### How to Run (Local Dev — Optional)

```bash
# Terminal 1: Backend (local)
cd /Users/alex/Documents/Code/Phoenix_Project
PYTHONPATH=. ./venv/bin/python3 backend/main.py
# → Runs on http://0.0.0.0:8001

# Terminal 2: Ollama (only needed if GROQ_API_KEY is not set)
ollama serve
# Model: qwen3:8b
```

The iOS app defaults to `https://phoenix-coach.onrender.com` (set in `NetworkManager.swift` line 22). The URL can be overridden in the Profile tab's connection settings and is persisted in UserDefaults.

---

## 3. Project Structure

```
Phoenix_Project/
├── backend/
│   ├── main.py                       # FastAPI app — ALL endpoints (946 lines)
│   ├── agents/
│   │   ├── data_agent.py             # Summarizes athlete state from DB (238 lines)
│   │   └── response_agent.py         # LLM prompts: weekly plan, adapt, analyze, review (732 lines)
│   ├── models/
│   │   └── database.py              # SQLAlchemy ORM models (8 tables, 163 lines)
│   ├── services/
│   │   ├── periodization_engine.py   # Phase detection, multi-distance timeline profiles, volume scaling (1665 lines)
│   │   ├── plan_normalizer.py        # Normalizes heterogeneous LLM JSON → canonical format
│   │   ├── compliance.py             # Matches actual activities to planned workouts
│   │   ├── coros_scraper.py          # Playwright-based COROS web scraper
│   │   ├── ingestion_service.py      # COROS JSON → DB ingestion pipeline
│   │   └── fit_importer.py           # .FIT file parser (legacy import)
│   └── core/
│       ├── llm_client.py            # LLM abstraction: Groq API (cloud) / Ollama (local fallback)
│       └── knowledge_base.py        # ChromaDB RAG singleton (graceful disable on Render)
├── ios/PhoenixCoach/PhoenixCoach/
│   ├── ContentView.swift             # Tab bar: Today, Coach, Journal, Recent, Profile
│   ├── DesignSystem.swift            # DS enum: colors, radii, tracking, animations, GlassCard modifiers
│   ├── Models/
│   │   └── Models.swift              # ALL Swift Codable structs (644 lines)
│   ├── Services/
│   │   ├── NetworkManager.swift      # HTTP client for all backend endpoints + cloud fallback logic
│   │   ├── LocalLLMManager.swift     # MLX on-device LLM for chat
│   │   ├── Formatters.swift          # Shared DateFormatter instances (ISO8601, display formats)
│   │   └── NotificationManager.swift # Push notification permissions and scheduling
│   └── Views/
│       ├── Today/
│       │   ├── TodayView.swift       # Main screen: week strip, mindset, workouts, compliance, charts
│       │   └── BlockCalendarView.swift  # Accordion timeline + WeeklyWorkoutsDetailSheet
│       ├── Chat/
│       │   └── CoachChatView.swift    # Backend-streamed LLM chat via SSE (streaming tokens from Groq)
│       ├── Dashboard/
│       │   ├── DashboardView.swift    # Journal placeholder (empty state)
│       │   └── ActivityDetailView.swift # Telemetry + Coach's Take AI review
│       ├── Feedback/
│       │   └── FeedbackView.swift    # Activity history list with sport filters and infinite scroll
│       └── Profile/
│           ├── ProfileView.swift     # Biometrics, race pickers, target time, start date, sport constraints
│           └── InjuryLogView.swift   # Injury tracking CRUD with status management
├── scripts/
│   ├── migrate_sqlite_to_postgres.py # One-time SQLite → PostgreSQL migration
│   ├── scraper_health_check.py       # COROS scraper diagnostics
│   └── ...                           # Various DB utilities (audit, import, rebuild)
├── knowledge/                        # RAG knowledge base (10 markdown files)
│   ├── periodization.md, workout_types.md, short_distance_training.md,
│   ├── hr_zones.md, recovery_rules.md, running_training.md,
│   ├── cycling_training.md, swimming_training.md, strength_training.md, tapering.md
├── phoenix_coach.db                  # SQLite database (local dev, ~140MB)
├── requirements.txt                  # Python dependencies for Render deployment
├── Procfile                          # Render/Railway start command
└── .env                              # COROS credentials, GROQ_API_KEY, etc.
```

---

## 4. Database Schema (SQLite)

| Table | Purpose | Key Fields |
|---|---|---|
| `athletes` | Single athlete profile | name, age, weight_kg, hr_max, hr_rest, vo2_max, hrv_baseline, swim/bike/run/strength_days, **training_start_date**, race_name, race_type, race_distance, race_date, **target_finish_time** |
| `activities` | Training sessions (548 total) | id, sport, start_time, duration_sec, distance_m, avg_hr, training_load, source, activity_name, lap_data (JSON), hr_zone_distribution (JSON) |
| `activity_records` | Per-second GPS/HR data | heart_rate, speed, cadence, lat/lon |
| `recovery_snapshots` | Daily COROS EvoLab metrics (109 days) | hrv_ms, resting_hr, ati, cti, tib, load_ratio, fatigue_state, recommend_tl_min/max |
| `weekly_plans` | Generated 7-day plans | week_start, plan_json (full normalized structure + `_context` + `weekly_review`), created_at, last_adapted |
| `athlete_feedback` | Post-workout RPE/soreness | rpe, motivation, soreness, strength_exercises (JSON) |
| `coaching_recommendations` | Daily AI coaching outputs | date, recommended_workout, rationale, adaptation_reason, coaching_note |
| `injury_logs` | Injury tracking | date_reported, body_part, status (Active/Recovering/Resolved), severity (1-10), affected_sports |

*Note: Bolded fields represent database columns fully operational on both backend and iOS.*

---

## 5. Backend API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Health check + LLM provider & connection status |
| `GET` | `/athlete` | Raw athlete ORM object |
| `GET` | `/athlete/profile` | Retrieve athlete biometrics, race target, and start date |
| `PUT` | `/athlete/profile` | Update athlete profile, availability days, goals, and coaching start date |
| `GET` | `/athlete/injuries` | List all injury log entries |
| `POST` | `/athlete/injuries` | Create a new injury log entry |
| `PUT` | `/athlete/injuries/{id}` | Update an existing injury (status, severity, notes) |
| `POST` | `/sync` | Import FIT files from `fit_examples/` directory |
| `GET` | `/dashboard` | All activities + recovery snapshots feed |
| `GET` | `/coaching` | Generate daily AI coaching recommendation (2-agent pipeline) |
| `GET` | `/training-context` | Computed periodization context snapshot (no LLM) |
| `GET` | `/weekly-plan` | Retrieve current week's plan (triggers AI generation if missing) |
| `POST` | `/weekly-plan/regenerate` | Wipe the current week's plan and compile a fresh one from fresh summaries |
| `POST` | `/weekly-plan/adapt-today` | Adapt today's workout based on RHR/HRV/muscle soreness metrics |
| `GET` | `/weekly-plan/status` | Current plan enriched with actual watch compliance and overlays |
| `GET` | `/block-calendar` | Calculate complete weekly timeline targets and actual plans from start to race |
| `POST` | `/pull-to-refresh` | Full sync: scrape COROS → ingest → generate coaching recommendation |
| `POST` | `/smart-refresh` | Morning action: scrape → ingest → evaluate recovery → auto-adapt if needed |
| `POST` | `/chat` | SSE streaming chat endpoint (tokens streamed via Groq/Ollama) |
| `POST` | `/chat-sync` | Synchronous chat fallback (full response at once) |
| `GET` | `/activity/{id}/analysis` | Request detailed LLM post-workout review |
| `POST` | `/feedback` | Save daily post-workout RPE, soreness, and journal logs |

---

## 6. Periodization Engine & Dynamic Multi-Distance Support

File: `backend/services/periodization_engine.py`

Computes the `TrainingContext` dict that sets the boundaries for all LLM prompts.

### 🚀 Distance-Aware Periodization Engine
The periodization engine dynamically selects training phases, volume ceilings (expected hours and running kilometers), and allowed/forbidden workouts based on the athlete's `race_distance` (`5k`, `10k`, `Half Marathon`, `Marathon`, `Sprint`, `Olympic` triathlons).

This resolves previous issues where a short distance goal (like a **5k sub-20 mins**) was forced into marathon volume guidelines, confusing the LLM coach and causing safe-fallback rest weeks.

### Key Distance Profiles
- **5k / 10k Specifics**:
  - **Peak Volume**: Scaled down to **25-35 km/week** (running) and **5-7 hours/week** (total time).
  - **Phases**: Maps to **Speed Build** (Phase 2, ⚡/hare icon 🐇) and **Taper + Race** (Phase 3).
  - **Quality Sessions**: Re-enables high-intensity speedwork like `VO2max Intervals`, `Tempo Runs`, and `Repetitions (R)` while strictly forbidding long fatigue-inducing workouts like `Marathon Pace Long Runs` or runs longer than 15 km.
- **Marathon Specifics**:
  - **Peak Volume**: **40-55 km/week** and **10-11 hours/week**.
  - **Phases**: Foundation, Marathon Base, Marathon Build, Marathon Peak, Taper.
  - **Quality Sessions**: Focuses heavily on marathon pace long runs, progressive runs, and high aerobic volumes.
- **Triathlon (Sprint/Olympic)**:
  - Balances all three sports (swim, bike, run) plus strength, utilizing dynamic phase definitions.
- **Unknown Distances**:
  - Automatically falls back to **Foundation-Only** training (`FOUNDATION_ONLY_PROFILE`) for safety.

---

## 7. Plan Normalizer & Decodable Safe-Guards

File: `backend/services/plan_normalizer.py`

This normalizer cleans up LLM-generated JSON into the strict format expected by the iOS Swift Decodable structs.

### JSON Crash Prevention
The iOS app expects `weekly_review` to be a string or nullable. We resolved a silent crash where the LLM returned `weekly_review` as a nested dictionary structure. The normalizer now catches this and formats the nested review dictionary (Went Well, Needs Attention, Motivation) into a clean, formatted markdown string before sending it to the client, fully protecting the app's decode pipeline.

---

## 8. iOS App Structure

### 5 Tabs (`ContentView.swift`):

1.  **Today (`TodayView.swift`)**:
    *   *API Connection Banner*: Pulsing dot representing active base URL endpoint status.
    *   *Block Calendar Banner*: Prominent purple shortcut linking to `BlockCalendarView`.
    *   *Plan Mindset Collapsible*: Expandable banner detailing the current training block focus, priorities, and previous week's performance. Hosts the **Regenerate Plan** action.
    *   *Horizontal Week Strip*: Monday-to-Sunday navigation tracking date numbers and completion status.
    *   *COROS Recovery Dashboard*: Renders RHR, HRV, sleep, and Form indexes.
    *   *AI Adaptation Tray*: "Adapt Today's Workout with AI" button with a pulsing sparkle icon.
    *   *Compliance Overlays*: Renders workouts with color-coded borders matching recorded activities (✅ Green for completed, ⚠️ Amber for partial, ❌ Red for mismatch).
    *   *Analytics Charts*: Interactive Swift Charts for HRV, RHR, and Load Ratio (7-day trailing). Tapping a telemetry card launches a `.catmullRom` interpolated line chart sheet.
2.  **Coach (`CoachChatView.swift`)**:
    *   Backend-streamed chat portal using SSE (tokens from Groq via `/chat` endpoint). Contains welcome chips, pulsing dot thinking status, and stream token rendering. Falls back to on-device MLX Llama-3.2-1B via `LocalLLMManager.swift` when offline.
3.  **Journal (`DashboardView.swift`)**:
    *   Empty state placeholder for a future training journal feature. Shows `ContentUnavailableView` with a book icon.
4.  **Recent (`FeedbackView.swift`)**:
    *   Watch history activity list with sport filter capsules (All, Run, Bike, Swim, Strength), infinite-scroll lazy loading (10 items at a time), and a 5-minute in-memory dashboard cache. Navigates to `ActivityDetailView.swift` for telemetry grids and AI review.
5.  **Profile (`ProfileView.swift`)**:
    *   Biometrics, segmented race type and distance pickers, target finish times, coaching start date picker, and weekly sport constraint matrixes. Implements **seamless background auto-saving** and **inline expandable dropdown-style dial wheels** (for Age, Weight, and Target Time). Features a live status feedback widget (spinner and green checkmark) in the toolbar. Links to `InjuryLogView.swift` for injury tracking.

### Design System (`DesignSystem.swift`)

Centralized `DS` enum providing colors (dark background, glassmorphism surfaces), corner radii, typography tracking, and spring animations. Includes `GlassPanelCard` and `.glassCard()` modifier used across all views for consistent glassmorphism styling.

---

## 9. Known Issues & Gotchas

1.  **Render cold starts**: The free-tier Render instance spins down after 15 minutes of inactivity. The first request after idle takes ~30-60 seconds while the container restarts. The iOS app handles this gracefully with its 180-second timeout and UserDefaults-cached dashboard/plan fallbacks.
2.  **Buffering log delay**: When uvicorn is started non-interactively in background tasks, print statements and request logs are buffered. They will not appear in log files immediately until the stream is flushed.
3.  **Render 512MB RAM limit**: Render free tier imposes a strict 512MB RAM limit. `knowledge_base.py` detects the `RENDER` environment variable and gracefully disables ChromaDB ONNX embeddings, falling back to an equivalent zero-memory keyword search.
4.  **Groq rate limits**: Groq free tier allows 30 requests/min and 100K tokens/day. Heavy plan generation or rapid consecutive requests could hit these limits. The app's usage pattern (~10-20 calls/day) stays well within bounds.
5.  **Backend URL persistence**: The iOS app persists the backend URL in UserDefaults. If the default URL changes (e.g., new Render deployment), the `resetToDefaultURL()` migration logic in `NetworkManager.swift` automatically clears the cached URL when it detects a default change.

---

## 10. What Was Recently Completed

### Profile Settings High-Fidelity UI Redesign & Auto-Saving
1.  **Frictionless Background Auto-Saving**: Removed the large bottom "Save Configuration" button and alert sheets. All settings (Name, Date, type/distance selectors, day constraints, and wheels) now save immediately in the background upon interaction.
2.  **Auto-Saving Toolbar Indicator**: Added a visual feedback widget in the trailing navigation toolbar (next to the wifi router indicator). Displays an active spinner during background requests and a green checkmark circle that fades away on success.
3.  **Inline Expandable Dial Pickers**: Replaced popup dialogs and simple text fields with inline expandable wheel picker sections for:
    *   **Age**: Dynamic single wheel (`10` to `99`).
    *   **Weight**: Double wheels for integer and decimal weights (`30.0` to `200.9` kg) to prevent parsing/float errors.
    *   **Target Time**: Three-column athletic elapsed duration wheels (Hours, Minutes, Seconds) mapping to API string durations.
4.  **Quiet Performance Branding Alignment**: Restored standard iOS `NavigationStack` behaviors with transparent background toolbar styling, keeping only connection widgets and removing redundant top logos.

### Block Calendar & Custom Start Dates
5.  **Coaching Start Date Migration**: Added `training_start_date` to DB profile schemas and created Toggle/DatePicker controls in `ProfileView.swift`. This instantly corrected the "Week 75" calendar visual bug back down to **Week 1**.
6.  **Accordion Phase Groups**: Completely rebuilt `BlockCalendarView.swift` to render collapsible accordions grouped by sport phase (Foundation, Base, Build, Peak, Taper) with dynamic phase-colored headers.
7.  **Volume & Workout Previews**: Added expected hourly/km preview capsules inside timeline rows, and built `WeeklyWorkoutsDetailSheet` displaying detailed daily workout checklists or future projected phase prescriptions.
8.  **HSL Muscle Badging**: Integrated scrolling visual capsule badges inside strength workout cards on iOS displaying targeted hypertrophy muscle groups (Chest, Shoulders, Back, Legs, Arms) color-coded using distinct, sleek HSL hues.
9.  **Plan Mindset & Regeneration**: Created the two-state mindset banner on the core dashboard, added a confirmation modal, and implemented the backend endpoint to overwrite active plans with newly compiled blueprints.
10. **Normalizer Decode Crash Fix**: Resolved a critical Swift decoder crash by converting nested dictionary weekly reviews into formatted markdown strings during normalization.
11. **Dynamic Periodization Engine (Multi-Distance Support)**: Fully refactored `backend/services/periodization_engine.py` to support distance-specific profiles (`5k`, `10k`, `Half Marathon`, `Marathon`, `Sprint`, `Olympic`). Added a new `speed_build` phase (⚡/hare icon 🐇) for 5k/10k plans, refactored allowed/forbidden menus to re-enable `VO2max Intervals` and `Tempo Runs` during speed build, and dynamically scaled expected hour/km volume targets based on race type. Also updated LLM system prompts in `response_agent.py` to adapt coaching instructions (Running coach vs. Triathlon coach) based on athlete objectives.

### UI & UX Polish Updates
- **Recent View & Pagination**: Renamed the feedback tab to "Recent", implemented infinite-scroll lazy loading (rendering 10 items at a time) in `FeedbackView.swift`, and introduced a 5-minute in-memory dashboard cache in `NetworkManager.swift` to prevent constant re-fetching on tab switch.
- **Today View Analytics Charts**: Added interactive Swift Charts for HRV, RHR, and Load Ratio based on 7-day trailing data in `TodayView.swift`. Tapping a telemetry card launches a half-screen sheet featuring a sleek `.catmullRom` interpolated line chart.
- **Profile Layout Fix**: Resolved an out-of-bounds horizontal rendering issue inside the settings cards by enforcing `minWidth` on native wheel pickers and condensing the weekly constraint matrix buttons.
- **Injury Log Formatting**: Formatted raw ISO8601 timestamp strings (e.g., `2023-10-25T...`) into human-readable `DateFormatter` strings (e.g., `Oct 25, 2023`).

### Cloud Migration (✅ Fully Completed)
12. **LLM Client Abstraction**: Created `backend/core/llm_client.py` to abstract LLM calls. Uses Groq API (`llama-3.3-70b-versatile`) in production; falls back to local Ollama (`qwen3:8b`) when `GROQ_API_KEY` is unset. Refactored all `ollama.chat()` call sites in `response_agent.py` and `main.py`.
13. **PostgreSQL Migration**: Migrated the entire SQLite database to a managed PostgreSQL instance on Render. Conditional `check_same_thread` in `main.py` ensures both SQLite and PostgreSQL compatibility.
14. **PostgreSQL Strict Typing Fixes**: Replaced SQLite-specific `julianday()` functions with cross-compatible Python `timedelta` windows for deduplication checks. Fixed a strict type-matching bug where integer `sport_code` was erroneously compared to string `sport` columns.
15. **Render Deployment**: Backend deployed to `https://phoenix-coach.onrender.com` with Procfile, requirements.txt, and environment variables. Free PostgreSQL instance provisioned.
16. **Render Memory Optimizations**: `knowledge_base.py` detects the `RENDER` environment variable and gracefully disables ChromaDB ONNX embeddings, falling back to zero-memory keyword search.
17. **Playwright Cloud Compatibility**: Configured Render to install Chromium via `playwright install chromium` and added `PLAYWRIGHT_BROWSERS_PATH=0` to persist the browser cache.
18. **iOS Backend Routing**: `NetworkManager.swift` now defaults to `https://phoenix-coach.onrender.com` with automatic URL migration logic and cloud fallback in `checkConnection()`.

### New Features & Infrastructure
19. **Smart Refresh Endpoint**: Added `/smart-refresh` — a single morning action that scrapes COROS, ingests data, evaluates recovery thresholds (HRV drop, elevated RHR, fatigue state, load ratio), and auto-adapts the workout if needed.
20. **Injury Log System**: Full CRUD for injury tracking with `InjuryLogView.swift` (iOS) and `/athlete/injuries` endpoints (backend). Tracks body part, severity, status (Active/Recovering/Resolved), and affected sports.
21. **Design System (`DesignSystem.swift`)**: Centralized `DS` enum with colors, radii, tracking, animations, and `GlassPanelCard`/`.glassCard()` modifiers. All views refactored to use the shared design tokens.
22. **Notification Manager**: Added `NotificationManager.swift` for push notification permissions and scheduling infrastructure.
23. **Formatters**: Created shared `Formatters.swift` with reusable `DateFormatter` instances used across all views.
24. **Ingestion Service**: Added `backend/services/ingestion_service.py` — standalone COROS JSON-to-DB pipeline used by `/pull-to-refresh` and `/smart-refresh`.

---

## 11. Suggested Next Steps & Goals

### High Priority: Capturing Activities & Scraper Health
-  **Capturing Activities Core Goal**: Establish the core importance of activity capturing in the feedback loop. Work on discussing and highlighting how the app records, syncs, and visualizes athletic telemetry data (speed, heart rate zones, GPS maps, training load) to feed the coaching engine.
-  **COROS scraper reliability**: Update Playwright login scripts in `coros_scraper.py` to add robust element selectors in case of site architecture updates.
-  **On-device Chat quality**: Integrate MLX on-device LLM prompts with richer RAG summaries to improve response depth.

### Medium Priority
-  **Soreness & Injury integration**: Feed logged soreness indices from the daily journal and feedback tables directly into the LLM adaptation prompt.
-  **Weekly compliance score calculation**: Write a deterministic matching script in `compliance.py` that outputs a weekly score badge based on duration tolerances.

### Lower Priority
-  **Apple Watch Integration**: Import Apple HealthKit files directly in addition to COROS.
-  **Multi-Athlete Tenant**: Remove hardcoded athlete ID 1 to support multiple local users.

---

## 12. Quick Reference

```bash
# Test cloud endpoint
curl https://phoenix-coach.onrender.com/health | python3 -m json.tool
curl https://phoenix-coach.onrender.com/training-context | python3 -m json.tool

# Start backend (local dev only)
cd /Users/alex/Documents/Code/Phoenix_Project
PYTHONPATH=. python3 backend/main.py

# Kill stuck port (local dev)
lsof -ti:8001 | xargs kill -9

# Check what plan is in DB (local SQLite only)
python3 -c "import sqlite3; c=sqlite3.connect('phoenix_coach.db').cursor(); print(c.execute('SELECT id, week_start FROM weekly_plans ORDER BY id DESC').fetchall())"

# Test local endpoint
curl http://localhost:8001/weekly-plan | python3 -m json.tool
curl http://localhost:8001/training-context | python3 -m json.tool
curl http://localhost:8001/block-calendar | python3 -m json.tool
```

---

## 13. Cloud Deployment (✅ Completed)

> **Completed**: 2026-07-23
> **Approach**: Hybrid — Groq free tier for heavy LLM tasks, on-device MLX Llama-3.2-1B stays for offline chat on iPhone.

### Live Architecture

```
┌─────────────────────────────────────────────────────────┐
│  iPhone (iOS App)                                       │
│  ├── TodayView, Coach, Journal, Recent, Profile         │
│  ├── On-device MLX chat (Llama 3.2 1B) — OFFLINE OK    │
│  └── NetworkManager → HTTPS to cloud backend            │
└────────────────────┬────────────────────────────────────┘
                     │ HTTPS
┌────────────────────▼────────────────────────────────────┐
│  Render (https://phoenix-coach.onrender.com)            │
│  ├── FastAPI backend (main.py)                          │
│  ├── PostgreSQL database (managed)                      │
│  ├── ChromaDB / keyword-search RAG (ONNX disabled)      │
│  ├── Playwright COROS scraper                            │
│  └── LLM Client → Groq API (Llama 3.3 70B free tier)   │
└────────────────────┬────────────────────────────────────┘
                     │ API calls
                     ▼
              Groq Cloud (free tier)
              └── llama-3.3-70b-versatile
```

### Key Implementation Details

| Component | Implementation |
|---|---|
| **LLM abstraction** | `backend/core/llm_client.py` — Groq via OpenAI SDK, Ollama fallback |
| **DB connection** | Conditional `check_same_thread` in `main.py` for SQLite/PostgreSQL compatibility |
| **Health endpoint** | Returns `{"backend": "ok", "llm": {"provider": "groq", "status": "connected", "model": "..."}}` |
| **iOS default URL** | `https://phoenix-coach.onrender.com` in `NetworkManager.swift` line 22 |
| **iOS health parsing** | Reads `json["llm"]["status"]` for the pulsing dot indicator |
| **Cloud RAM limit** | `knowledge_base.py` disables ONNX when `RENDER` env var is set |

### Environment Variables (Render Dashboard)

```bash
DATABASE_URL=postgresql://user:pass@host:5432/phoenix_coach   # Auto-provisioned by Render
GROQ_API_KEY=gsk_xxxxxxxx                                     # From https://console.groq.com/keys
COACHING_MODEL=llama-3.3-70b-versatile
RENDER=true                                                    # Triggers memory-safe RAG mode
COROS_EMAIL=alex@example.com
COROS_PASSWORD=xxxxx
PYTHONPATH=.
PLAYWRIGHT_BROWSERS_PATH=0
```

### Deployment Files

- **`requirements.txt`**: fastapi, uvicorn, sqlalchemy, psycopg2-binary, openai, chromadb, playwright, pydantic, python-dotenv, etc.
- **`Procfile`**: `web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- **Build command**: `pip install -r requirements.txt && playwright install chromium --with-deps`

### Monthly Cost

| Component | Monthly Cost |
|---|---|
| Groq free tier (Llama 3.3 70B) | **$0** |
| Render Free Web Service + Free PostgreSQL | **$0** |
| **Total** | **$0/month** |

### Rollback Plan

If anything goes wrong, the original local setup still works:
1. Keep `ollama` installed locally
2. `llm_client.py` auto-falls back to Ollama when `GROQ_API_KEY` is not set
3. SQLite database is still on disk
4. iOS app URL can be changed back in Profile settings (persisted in UserDefaults)
