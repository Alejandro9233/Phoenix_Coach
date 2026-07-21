# 🏃‍♂️ Phoenix Coach — Complete Project Handoff

> **Last Updated**: 2026-07-20
> **Author**: Alex (athlete/developer)
> **Status**: MVP functional, actively iterating on premium features. Cloud migration plan in Section 13.

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
| **Backend** | Python FastAPI | Single file `backend/main.py`, runs on port **8001** |
| **Database** | SQLite | `phoenix_coach.db` in project root (~140MB with full history) |
| **LLM (backend)** | Ollama + `qwen3:8b` | Local Mac, used for plan generation & analysis |
| **RAG** | ChromaDB + markdown knowledge base | `knowledge/` dir (10 files: periodization, HR zones, workout types, etc.) |
| **Data Source** | COROS watch → Playwright scraper | Scrapes activities, EvoLab metrics, recovery data |

### How to Run

```bash
# Terminal 1: Backend
cd /Users/alex/Documents/Code/Phoenix_Project
PYTHONPATH=. ./venv/bin/python3 backend/main.py
# → Runs on http://0.0.0.0:8001

# Terminal 2: Ollama (must be running for LLM features)
ollama serve
# Model: qwen3:8b

# Xcode: Open ios/PhoenixCoach/PhoenixCoach.xcodeproj
# Build and run on device/simulator
```

The iOS app connects to the Mac backend at `http://192.168.0.107:8001` (hardcoded in `NetworkManager.swift` line 10). If the Mac's IP changes, update this.

---

## 3. Project Structure

```
Phoenix_Project/
├── backend/
│   ├── main.py                    # FastAPI app — ALL endpoints (853 lines)
│   ├── agents/
│   │   ├── data_agent.py          # Summarizes athlete state from DB
│   │   └── response_agent.py      # LLM prompts: weekly plan, adapt, analyze, review
│   ├── models/
│   │   └── database.py            # SQLAlchemy ORM models (7 tables)
│   ├── services/
│   │   ├── periodization_engine.py  # Phase detection, multi-distance timeline profiles, volume scaling
│   │   ├── plan_normalizer.py       # Normalizes heterogeneous LLM JSON → canonical format
│   │   ├── compliance.py            # Matches actual activities to planned workouts
│   │   ├── coros_scraper.py         # Playwright-based COROS web scraper
│   │   └── fit_importer.py          # .FIT file parser (legacy import)
│   └── core/
│       └── knowledge_base.py      # ChromaDB RAG singleton
├── ios/PhoenixCoach/PhoenixCoach/
│   ├── ContentView.swift          # Tab bar: Today, Coach, Log, Feedback, Profile
│   ├── Models/
│   │   └── Models.swift           # ALL Swift Codable structs (520 lines)
│   ├── Services/
│   │   ├── NetworkManager.swift   # HTTP client for all backend endpoints
│   │   └── LocalLLMManager.swift  # MLX on-device LLM for chat
│   └── Views/
│       ├── Today/
│       │   ├── TodayView.swift    # Main screen: week strip, mindset, workouts, compliance
│       │   └── BlockCalendarView.swift  # Accordion timeline + WeeklyWorkoutsDetailSheet
│       ├── Chat/
│       │   └── CoachChatView.swift # On-device LLM chat with streaming
│       ├── Dashboard/
│       │   ├── DashboardView.swift # Activity history list
│       │   └── ActivityDetailView.swift # Telemetry + Coach's Take AI review
│       ├── Feedback/
│       │   └── FeedbackView.swift # Post-workout RPE/soreness daily journal
│       └── Profile/
│           └── ProfileView.swift  # Biometrics, segmented race type and distance pickers, target finish times, coaching start date picker, and weekly sport constraint matrixes
├── knowledge/                     # RAG knowledge base (10 markdown files)
│   ├── periodization.md           # Phase definitions, volume tables
│   ├── workout_types.md           # Workout catalog with descriptions
│   ├── short_distance_training.md # 5k/10k training physiology & key workouts
│   └── hr_zones.md, recovery_rules.md, running_training.md, etc.
├── phoenix_coach.db               # SQLite database (the active one!)
└── .env                           # COROS credentials for scraper
```

---

## 4. Database Schema (SQLite)

| Table | Purpose | Key Fields |
|---|---|---|
| `athletes` | Single athlete profile | name, age, weight_kg, hr_max, hr_rest, vo2_max, hrv_baseline, swim/bike/run/strength_days, **training_start_date**, race_name, race_type, race_distance, race_date, **target_finish_time** |
| `activities` | Training sessions (532 total) | id, sport, start_time, duration_sec, distance_m, avg_hr, training_load, source |
| `activity_records` | Per-second GPS/HR data | heart_rate, speed, cadence, lat/lon |
| `recovery_snapshots` | Daily COROS EvoLab metrics (95 days) | hrv_ms, resting_hr, ati, cti, tib, load_ratio, recommend_tl_min/max |
| `weekly_plans` | Generated 7-day plans | week_start, plan_json (full normalized structure + `_context` + `weekly_review`), created_at |
| `athlete_feedback` | Post-workout RPE/soreness | rpe, motivation, soreness, strength_exercises (JSON) |

*Note: Bolded fields represent database columns fully operational on both backend and iOS.*

---

## 5. Backend API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Health check + Ollama & GPU runner status |
| `GET` | `/athlete/profile` | Retrieve athlete biometrics, race target, and start date |
| `PUT` | `/athlete/profile` | Update athlete profile, availability days, goals, and coaching start date |
| `GET` | `/dashboard` | All activities + recovery snapshots feed |
| `GET` | `/training-context` | Computed periodization context snapshot (no LLM) |
| `GET` | `/weekly-plan` | Retrieve current week's plan (triggers AI generation if missing) |
| `POST` | `/weekly-plan/regenerate` | Wipe the current week's plan and compile a fresh one from fresh summaries |
| `POST` | `/weekly-plan/adapt-today` | Adapt today's workout based on RHR/HRV/muscle soreness metrics |
| `GET` | `/weekly-plan/status` | Current plan enriched with actual watch compliance and overlays |
| `GET` | `/block-calendar` | Calculate complete weekly timeline targets and actual plans from start to race |
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
2.  **Coach (`CoachChatView.swift`)**:
    *   On-device conversation portal running Llama-3.2-1B on the CPU. Contains welcome chips, pulsing dot thinking status, and stream token rendering.
3.  **Log (`FeedbackView.swift`)**:
    *   "Daily Journal" where the athlete writes open notes about sleep, soreness, or fatigue which get analyzed by the coach.
4.  **Feedback (`DashboardView.swift`)**:
    *   Watch history activity list with pagination and a detail screen (`ActivityDetailView.swift`) which prints telemetry grids and rating cards.
5.  **Profile (`ProfileView.swift`)**:
    *   Biometrics, segmented race type and distance pickers, target finish times, coaching start date picker, and weekly sport constraint matrixes. Implements **seamless background auto-saving** and **inline expandable dropdown-style dial wheels** (for Age, Weight, and Target Time) to replace cumbersome popups and text fields. Features a live status feedback widget (spinner and green checkmark) in the toolbar.

---

## 9. Known Issues & Gotchas

1.  **Buffering log delay**: When uvicorn is started non-interactively in background tasks, print statements and request logs are buffered. They will not appear in log files immediately until the stream is flushed.
2.  **IP Hardcoding**: The iOS app connects to `http://192.168.0.107:8001` (hardcoded in `NetworkManager.swift` line 10). When switching local networks, this must be updated or configured inside the Profile connection settings tab.
3.  **Model pre-evaluation delay**: On local M-series Macs, Qwen 8B can take 3 to 8 minutes to evaluate long prompt contexts (such as plans containing extensive database histories and RAG knowledge base chunks). This is normal; the connection remains established and completes cleanly.

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

### Cloud Migration (Render + PostgreSQL + Groq)
12. **LLM Client Abstraction**: Rewrote AI integrations in `llm_client.py` and `response_agent.py` to transition from local Ollama (`qwen3:8b`) to cloud-hosted Groq API (`llama-3.3-70b-versatile`). 
13. **PostgreSQL Migration**: Migrated the entire SQLite database to a managed PostgreSQL instance on Render. 
14. **PostgreSQL Strict Typing Fixes**: Replaced SQLite-specific `julianday()` functions with cross-compatible Python `timedelta` windows for deduplication checks. Fixed a strict type-matching bug where integer `sport_code` was erroneously compared to string `sport` columns.
15. **Render Memory Optimizations**: Render free tier imposes a strict 512MB RAM limit. To prevent OOM crashes, `knowledge_base.py` was updated to detect the `RENDER` environment variable and gracefully disable ChromaDB ONNX embeddings, falling back to an equivalent zero-memory keyword search.
16. **Playwright Cloud Compatibility**: Configured Render to successfully install Chromium via standard `playwright install chromium` builds and added `PLAYWRIGHT_BROWSERS_PATH=0` to the environment variables to persist the browser cache into the run phase.
17. **iOS Backend Routing**: Updated `NetworkManager.swift` to point to `https://phoenix-coach.onrender.com` instead of hardcoded local Mac IP addresses, fully decoupling the app from the local machine.

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
# Start backend
cd /Users/alex/Documents/Code/Phoenix_Project
PYTHONPATH=. python3 backend/main.py

# Kill stuck port
lsof -ti:8001 | xargs kill -9

# Check what plan is in DB
python3 -c "import sqlite3; c=sqlite3.connect('phoenix_coach.db').cursor(); print(c.execute('SELECT id, week_start FROM weekly_plans ORDER BY id DESC').fetchall())"

# Delete current week's plan (to force regeneration)
python3 -c "import sqlite3; c=sqlite3.connect('phoenix_coach.db'); c.execute('DELETE FROM weekly_plans WHERE week_start=\"2026-05-25\"'); c.commit()"

# Test endpoint
curl http://localhost:8001/weekly-plan | python3 -m json.tool
curl http://localhost:8001/training-context | python3 -m json.tool
curl http://localhost:8001/block-calendar | python3 -m json.tool
```

---

## 13. Cloud Migration Implementation Plan

> **Goal**: Move the backend off the Mac so Alex only needs his iPhone. Eliminate the daily "start the backend + Ollama" friction.
> **Decision Date**: 2026-07-20
> **Approach**: Option C (Hybrid) — Groq free tier for heavy LLM tasks, on-device MLX Llama-3.2-1B stays for offline chat on iPhone.

### Architecture After Migration

```
┌─────────────────────────────────────────────────────────┐
│  iPhone (iOS App)                                       │
│  ├── TodayView, Coach, Dashboard, Feedback, Profile     │
│  ├── On-device MLX chat (Llama 3.2 1B) — OFFLINE OK    │
│  └── NetworkManager → HTTPS to cloud backend            │
└────────────────────┬────────────────────────────────────┘
                     │ HTTPS
┌────────────────────▼────────────────────────────────────┐
│  Cloud (Railway / Render)                               │
│  ├── FastAPI backend (main.py)                          │
│  ├── PostgreSQL database                                │
│  ├── ChromaDB / in-memory RAG                           │
│  ├── Playwright COROS scraper (daily cron)               │
│  └── LLM Client → Groq API (Llama 3.3 70B free tier)   │
└────────────────────┬────────────────────────────────────┘
                     │ API calls
                     ▼
              Groq Cloud (free tier)
              └── llama-3.3-70b-versatile
```

### Groq Free Tier Limits (verified 2026-07-20)

| Limit | Value | Our Usage |
|---|---|---|
| Requests/min | 30 | ~1-2 at a time ✅ |
| Requests/day | 1,000 | ~10-20 max ✅ |
| Tokens/min | 12,000 | One heavy call at a time ✅ |
| Tokens/day | 100,000 | ~15-20 full calls/day ✅ |

**Important**: Groq uses the OpenAI-compatible API format. Use the `openai` Python SDK pointed at `https://api.groq.com/openai/v1`.

---

### Phase 1: LLM Client Abstraction Layer

**Goal**: Replace all direct `ollama.chat()` calls with an abstracted LLM client that can target Groq (cloud) or Ollama (local dev). This is the largest single change.

#### Step 1.1: Create `backend/core/llm_client.py` [NEW FILE]

```python
"""
LLM Client — Abstraction layer over Groq API (cloud) and Ollama (local dev).

Uses the OpenAI-compatible SDK pointed at Groq's endpoint.
Falls back to Ollama if GROQ_API_KEY is not set (local dev mode).
"""
import os
import json
from openai import OpenAI

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("COACHING_MODEL", "llama-3.3-70b-versatile")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

def _use_groq() -> bool:
    """Return True if we should use Groq cloud, False for local Ollama."""
    return bool(GROQ_API_KEY)

def _get_groq_client() -> OpenAI:
    """Return an OpenAI client pointed at Groq's API."""
    return OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )

def chat_completion(messages: list[dict], json_mode: bool = False) -> str:
    """
    Send a chat completion request to Groq (cloud) or Ollama (local).

    Args:
        messages: List of {"role": ..., "content": ...} dicts.
        json_mode: If True, request JSON output format.

    Returns:
        The assistant's response content as a string.
    """
    if _use_groq():
        return _groq_chat(messages, json_mode)
    else:
        return _ollama_chat(messages, json_mode)

def _groq_chat(messages: list[dict], json_mode: bool) -> str:
    """Call Groq via OpenAI-compatible SDK."""
    client = _get_groq_client()
    kwargs = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.7,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content

def _ollama_chat(messages: list[dict], json_mode: bool) -> str:
    """Call local Ollama (for development/testing only)."""
    import ollama
    kwargs = {
        "model": OLLAMA_MODEL,
        "messages": messages,
    }
    if json_mode:
        kwargs["format"] = "json"

    response = ollama.chat(**kwargs)
    content = response["message"]["content"]

    # Strip Qwen3 thinking tags
    if "<think>" in content:
        content = content.split("</think>")[-1].strip()
    return content

async def chat_completion_stream(messages: list[dict]):
    """
    Async generator that yields tokens for streaming responses.
    Used by the /chat SSE endpoint.
    """
    if _use_groq():
        async for token in _groq_stream(messages):
            yield token
    else:
        async for token in _ollama_stream(messages):
            yield token

async def _groq_stream(messages: list[dict]):
    """Stream tokens from Groq."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    stream = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        stream=True,
        temperature=0.7,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content

async def _ollama_stream(messages: list[dict]):
    """Stream tokens from local Ollama (dev mode)."""
    import ollama
    client = ollama.AsyncClient()
    inside_think = False
    think_buffer = ""

    async for chunk in await client.chat(
        model=OLLAMA_MODEL,
        messages=messages,
        stream=True,
    ):
        token = chunk["message"]["content"]
        if not token:
            continue

        # Handle <think> tag suppression (same logic as current main.py)
        if inside_think:
            think_buffer += token
            if "</think>" in think_buffer:
                after = think_buffer.split("</think>", 1)[1]
                inside_think = False
                think_buffer = ""
                if after.strip():
                    yield after
            continue

        if "<think>" in token:
            parts = token.split("<think>", 1)
            if parts[0]:
                yield parts[0]
            inside_think = True
            think_buffer = parts[1] if len(parts) > 1 else ""
            if "</think>" in think_buffer:
                after = think_buffer.split("</think>", 1)[1]
                inside_think = False
                think_buffer = ""
                if after.strip():
                    yield after
            continue

        yield token

def check_llm_available() -> dict:
    """Health check for the LLM backend. Returns status dict."""
    if _use_groq():
        try:
            client = _get_groq_client()
            # Minimal test call
            client.models.list()
            return {"provider": "groq", "status": "connected", "model": GROQ_MODEL}
        except Exception as e:
            return {"provider": "groq", "status": "error", "detail": str(e)}
    else:
        try:
            import ollama
            ollama.show(OLLAMA_MODEL)
            return {"provider": "ollama", "status": "connected", "model": OLLAMA_MODEL}
        except Exception:
            return {"provider": "ollama", "status": "disconnected", "model": OLLAMA_MODEL}
```

#### Step 1.2: Refactor `backend/agents/response_agent.py`

**What changes**: Replace all 6 `ollama.chat()` call sites with `llm_client.chat_completion()`. Remove the `import ollama` and `OLLAMA_AVAILABLE` flag. Remove `<think>` tag stripping (handled inside `llm_client`).

**Lines to change (6 call sites)**:

| Call Site | Method | Lines | What it does |
|---|---|---|---|
| 1 | `generate_recommendation()` | 206-214 | Daily recommendation |
| 2 | `analyze_activity()` | 281-289 | Post-workout "Coach's Take" |
| 3 | `_generate_plan_with_context()` | 422-430 | Weekly plan (with periodization) |
| 4 | `_generate_plan_legacy()` | 501-509 | Weekly plan (legacy fallback) |
| 5 | `adapt_daily()` | 581-589 | Adapt today's workout based on recovery |
| 6 | `generate_weekly_review()` | 635-643 | End-of-week review |

**Pattern for each call site** — replace this:

```python
# BEFORE (all 6 sites follow this pattern)
if not OLLAMA_AVAILABLE:
    return self._fallback_xxx(...)

try:
    response = ollama.chat(
        model=self.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        format="json"
    )
    content = response["message"]["content"]
    if "<think>" in content:
        content = content.split("</think>")[-1].strip()
    return json.loads(content)
except ...:
```

With this:

```python
# AFTER (all 6 sites)
from backend.core.llm_client import chat_completion

try:
    content = chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        json_mode=True
    )
    return json.loads(content)
except ...:
```

**Additional changes in response_agent.py**:
- **Lines 17-21**: Remove `import ollama` / `OLLAMA_AVAILABLE` block entirely.
- **Line 176**: Change `def __init__(self, model="qwen3:8b")` to `def __init__(self)` — model is now configured via env vars in `llm_client.py`.
- **Line 177**: Remove `self.model = model`.

#### Step 1.3: Refactor `backend/main.py`

**Ollama references to remove/replace**:

| Lines | What | Action |
|---|---|---|
| 27-28 | `_ollama_available`, `OLLAMA_MODEL` globals | Remove |
| 40-51 | `check_ollama()` function | Replace with `from backend.core.llm_client import check_llm_available` |
| 61-77 | Ollama warmup in `lifespan()` | Replace with LLM health check (no warmup needed for API) |
| 130-138 | `/health` endpoint | Use `check_llm_available()` |
| 691-811 | `/chat` streaming endpoint | Replace `ollama.AsyncClient()` streaming with `chat_completion_stream()` from `llm_client` |
| 814-862 | `/chat-sync` endpoint | Replace `ollama.AsyncClient().chat()` with `chat_completion()` from `llm_client` |
| All `ResponseAgent()` instantiations (lines 271, 347, 491, 556, 957) | `ResponseAgent()` | No change needed — constructor no longer takes model param |

**`/chat` endpoint rewrite** (lines 737-810):

```python
# AFTER — replaces the entire event_stream() inner function
async def event_stream():
    """Generator that yields SSE events with streamed tokens."""
    try:
        from backend.core.llm_client import chat_completion_stream
        async for token in chat_completion_stream(messages):
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        print(f"LLM STREAMING ERROR: {e}")
        fallback = f"Coach is temporarily unavailable. Error: {str(e)[:100]}"
        yield f"data: {json.dumps({'token': fallback})}\n\n"
        yield "data: [DONE]\n\n"
```

**`/chat-sync` endpoint rewrite** (lines 847-862):

```python
# AFTER
try:
    from backend.core.llm_client import chat_completion
    content = chat_completion(messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message}
    ])
    return {"response": content}
except Exception as e:
    print(f"LLM ERROR in /chat-sync: {e}")
    return {"response": f"I'm currently in offline mode. Error: {str(e)[:100]}"}
```

**`/health` endpoint rewrite** (lines 130-138):

```python
@app.get("/health")
async def health_check():
    from backend.core.llm_client import check_llm_available
    llm_status = check_llm_available()
    return {
        "backend": "ok",
        "llm": llm_status,
    }
```

**Note for iOS**: The iOS app reads `health.ollama` as "connected"/"disconnected" for the pulsing dot indicator. After the migration, it should read `health.llm.status` instead. Update `NetworkManager.swift` `checkConnection()` method accordingly (see Phase 4).

---

### Phase 2: Database Migration (SQLite → PostgreSQL)

**Goal**: Move from file-based SQLite to managed PostgreSQL for cloud hosting.

#### Step 2.1: Update `backend/main.py` database setup (lines 18-20)

```python
# BEFORE
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./phoenix_coach.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# AFTER
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./phoenix_coach.db")
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
engine = create_engine(DATABASE_URL, connect_args=connect_args)
```

The `check_same_thread` argument is SQLite-only and will crash with PostgreSQL. This conditional makes the code work with both.

#### Step 2.2: Install psycopg2 for PostgreSQL support

Add `psycopg2-binary` to `requirements.txt` (see Phase 3).

#### Step 2.3: Data migration script — `scripts/migrate_sqlite_to_postgres.py` [NEW FILE]

```python
"""
One-time migration: dump SQLite data → PostgreSQL.

Usage:
    POSTGRES_URL="postgresql://..." python scripts/migrate_sqlite_to_postgres.py

Reads from local phoenix_coach.db, writes to the Postgres instance.
"""
import os
from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import sessionmaker

SQLITE_URL = "sqlite:///./phoenix_coach.db"
POSTGRES_URL = os.environ["POSTGRES_URL"]

sqlite_engine = create_engine(SQLITE_URL)
pg_engine = create_engine(POSTGRES_URL)

# Reflect all tables from SQLite
meta = MetaData()
meta.reflect(bind=sqlite_engine)

# Create tables in Postgres
meta.create_all(bind=pg_engine)

# Copy data table by table
with sqlite_engine.connect() as src, pg_engine.connect() as dst:
    for table in meta.sorted_tables:
        rows = src.execute(table.select()).fetchall()
        if rows:
            # Convert to list of dicts
            cols = [c.name for c in table.columns]
            data = [dict(zip(cols, row)) for row in rows]
            dst.execute(table.insert(), data)
            dst.commit()
            print(f"  Migrated {len(data)} rows → {table.name}")
        else:
            print(f"  {table.name}: empty, skipped")

print("Migration complete!")
```

**Important**: The `activity_records` table is large (~140MB of per-second GPS data). This migration may take a few minutes. Run it from the Mac where the SQLite file lives.

#### Step 2.4: SQLAlchemy model compatibility

The current ORM models in `backend/models/database.py` use **generic SQLAlchemy types** (`String`, `Integer`, `Float`, `JSON`, `Date`, `DateTime`, `Text`, `Boolean`). These are all Postgres-compatible — **no changes needed** to `database.py`.

The one exception: `Column(JSON)` is used in 4 places (lines 60, 66, 67, 129, 149). PostgreSQL natively supports JSON columns, so this works fine. If any query uses `json_extract` (SQLite-specific), it would need to change to Postgres JSONB operators, but the current codebase uses SQLAlchemy ORM exclusively — no raw JSON queries.

---

### Phase 3: Cloud Deployment Setup

#### Step 3.1: Create `requirements.txt` [NEW FILE]

```
fastapi==0.136.1
uvicorn==0.46.0
sqlalchemy==2.0.49
python-dotenv==1.2.2
pydantic==2.13.4
httpx==0.28.1
openai>=1.40.0
chromadb==1.5.9
playwright==1.59.0
psycopg2-binary>=2.9.0
```

**Note**: `ollama` is intentionally omitted from cloud requirements. It's only needed for local dev. The `openai` SDK is the new dependency (used by `llm_client.py` to talk to Groq).

#### Step 3.2: Create `Procfile` [NEW FILE]

```
web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

#### Step 3.3: Create `runtime.txt` [NEW FILE] (optional, pins Python version)

```
python-3.12
```

#### Step 3.4: Environment variables for cloud

Set these in Railway/Render dashboard:

```bash
# Required
DATABASE_URL=postgresql://user:pass@host:5432/phoenix_coach  # auto-provisioned by Railway
GROQ_API_KEY=gsk_xxxxxxxx  # from https://console.groq.com/keys
COACHING_MODEL=llama-3.3-70b-versatile
COROS_EMAIL=alex@example.com
COROS_PASSWORD=xxxxx
SECRET_KEY=generate-a-random-key

# Optional
PYTHONPATH=.
```

#### Step 3.5: Railway-specific deployment

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and initialize
railway login
railway init

# Add PostgreSQL
railway add --plugin postgresql

# Set env vars
railway variables set GROQ_API_KEY=gsk_xxx COACHING_MODEL=llama-3.3-70b-versatile ...

# Deploy
railway up
```

Or use Render:
1. Connect GitHub repo
2. Set build command: `pip install -r requirements.txt && playwright install chromium --with-deps`
3. Set start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
4. Add Postgres (free tier or $7/mo starter)
5. Set env vars in dashboard

#### Step 3.6: Playwright in cloud container

Playwright needs Chromium installed. Add a `build.sh` or use the build command:

```bash
pip install -r requirements.txt
playwright install chromium --with-deps
```

Railway/Render runs on Linux, so `--with-deps` installs the required system libraries.

---

### Phase 4: iOS App Update

#### Step 4.1: Update `NetworkManager.swift` base URL

File: `ios/PhoenixCoach/PhoenixCoach/Services/NetworkManager.swift`

**Lines 22-27** — Update the default URL for the physical device:

```swift
// BEFORE
#if targetEnvironment(simulator)
defaultURL = "http://127.0.0.1:8001"
#else
defaultURL = "http://10.22.181.143:8001"
#endif

// AFTER
#if targetEnvironment(simulator)
defaultURL = "http://127.0.0.1:8001"  // For local dev
#else
defaultURL = "https://phoenix-coach-production.up.railway.app"  // Cloud backend
#endif
```

**Also update** lines 51-56 (`resetToDefaultURL()`) with the same change.

#### Step 4.2: Update health check parsing

The `/health` response format changes from:

```json
{"backend": "ok", "ollama": "connected", "model": "qwen3:8b"}
```

To:

```json
{"backend": "ok", "llm": {"provider": "groq", "status": "connected", "model": "llama-3.3-70b-versatile"}}
```

Find where `isOllamaConnected` is set in `checkConnection()` and update the JSON key path. Search for `ollama` in `NetworkManager.swift` to find all references.

#### Step 4.3: (Optional) Add API Key header for security

Since the backend will be publicly accessible, add a simple API key:

**Backend** (`main.py`): Add middleware or dependency:

```python
API_KEY = os.getenv("PHOENIX_API_KEY", "")

async def verify_api_key(request: Request):
    if API_KEY and request.headers.get("X-API-Key") != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
```

**iOS** (`NetworkManager.swift`): Add header to all requests:

```swift
request.setValue("your-api-key-here", forHTTPHeaderField: "X-API-Key")
```

Store the key in the iOS app's `Info.plist` or as a build configuration constant.

---

### Phase 5: COROS Scraper Automation (Optional)

Currently, COROS syncing is triggered manually or on-demand. In the cloud, set up a daily cron job.

#### Step 5.1: Railway Cron Job

Railway supports cron services. Create a separate service with:
- **Command**: `python -m backend.services.coros_scraper` (or a new `scripts/sync_coros.py` wrapper)
- **Schedule**: `0 6 * * *` (daily at 6 AM UTC)

#### Step 5.2: Alternative — In-app scheduler

Add a background task in `main.py`:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job('cron', hour=6)
async def daily_coros_sync():
    """Auto-sync COROS data every morning."""
    scraper = CorosScraper()
    # ... run scrape and ingest
```

---

### What Stays Unchanged

| Component | Status |
|---|---|
| `backend/services/periodization_engine.py` (85KB) | No changes — pure Python math |
| `backend/services/plan_normalizer.py` | No changes — JSON normalization |
| `backend/services/compliance.py` | No changes — workout matching |
| `backend/agents/data_agent.py` | No changes — DB queries |
| `backend/core/knowledge_base.py` | No changes — ChromaDB works in cloud |
| `knowledge/*.md` (10 RAG files) | No changes — deployed with the app |
| `ios/.../LocalLLMManager.swift` | No changes — on-device chat stays |
| `ios/.../CoachChatView.swift` | No changes — already uses on-device MLX |
| All other iOS views | No changes (unless health check response format breaks parsing) |

---

### Migration Checklist (In Order)

```
[x] 1. Sign up for Groq free account at https://console.groq.com
[x] 2. Generate API key at https://console.groq.com/keys
[x] 3. Create backend/core/llm_client.py (new file from Phase 1.1)
[x] 4. Refactor response_agent.py — replace 6 ollama.chat() calls (Phase 1.2)
[x] 5. Refactor main.py — remove Ollama globals, update /health, /chat, /chat-sync (Phase 1.3)
[x] 6. Test locally: GROQ_API_KEY=gsk_xxx PYTHONPATH=. python3 backend/main.py
[x] 7. Verify all endpoints work with Groq (plan gen, adapt, analyze, chat)
[x] 8. Fix main.py DATABASE_URL conditional for Postgres compatibility (Phase 2.1)
[x] 9. Create requirements.txt (Phase 3.1)
[x] 10. Create Procfile (Phase 3.2)
[x] 11. Deploy to Railway/Render (Phase 3.5)
[x] 12. Provision PostgreSQL on Railway/Render
[x] 13. Run SQLite → Postgres migration script from Mac (Phase 2.3)
[x] 14. Set all env vars in cloud dashboard (Phase 3.4)
[x] 15. Verify cloud deployment: curl https://your-app.up.railway.app/health
[x] 16. Update iOS NetworkManager.swift base URL (Phase 4.1)
[x] 17. Update iOS health check parsing for new response format (Phase 4.2)
[x] 18. Build and test iOS app against cloud backend
[ ] 19. (Optional) Add API key authentication (Phase 4.3)
[ ] 20. (Optional) Set up COROS daily cron job (Phase 5)
```

### Estimated Cost After Migration

| Component | Monthly Cost |
|---|---|
| Groq free tier (Llama 3.3 70B) | **$0** |
| Railway Hobby (backend + Postgres) | **~$5-7** |
| **Total** | **~$5-7/month** |

### Rollback Plan

If anything goes wrong, the original local setup still works:
1. Keep `ollama` installed locally
2. `llm_client.py` auto-falls back to Ollama when `GROQ_API_KEY` is not set
3. SQLite database is still on disk
4. iOS app URL can be changed back in Profile settings (persisted in UserDefaults)
