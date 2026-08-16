---
description: Triage, fix, and verify a batch of Phoenix Coach bugs/changes across the FastAPI backend and SwiftUI iOS app, with backend tests and Simulator screenshots
---

# Phoenix Coach Bug Batch Workflow

## Context (do not skip)
This is a two-part system:
- **Backend**: FastAPI, single file `backend/main.py` + `agents/`, `services/`, `models/`,
  `core/`. Deployed on Render at https://phoenix-coach.onrender.com. Local dev on port 8001.
- **iOS**: Native SwiftUI at `ios/PhoenixCoach/`. Views live under
  `ios/PhoenixCoach/PhoenixCoach/Views/{Today,Chat,Dashboard,Feedback,Profile}/`.
  There is no browser — UI verification must use the iOS Simulator, not a browser subagent.

Known view → file mapping (use this instead of asking me):
- "Recent" = `Views/Feedback/FeedbackView.swift`
- "Today" = `Views/Today/TodayView.swift` (+ `BlockCalendarView.swift`)
- "Profile" = `Views/Profile/ProfileView.swift` (+ `InjuryLogView.swift`)
- "Session Review" = `Views/Dashboard/ActivityDetailView.swift`
- Weekly sport constraints = `ProfileView.swift` (iOS) + `PUT /athlete/profile` (backend, in `main.py`)

## Step 1 — Triage (no code yet)
Given the bug/change list provided as $ARGUMENTS:
1. Categorize each item as BACKEND (main.py/services/agents), iOS-UI (SwiftUI view/layout),
   or iOS-LOGIC (state, networking, data mapping).
2. Map each item to its actual file(s) using the mapping above — don't guess paths.
3. State a root-cause hypothesis for each functional item. If an item is marked
   "previously reported, not fixed" (e.g. the weekly constraints empty-days bug),
   explicitly check `PUT /athlete/profile` in `main.py` and the corresponding
   Codable struct in `Models.swift` for how empty arrays are serialized/deserialized —
   this is a common source of "empty means no-op" bugs.
4. Note which items are safe to parallelize via subagents (e.g. backend fix vs.
   independent iOS view fix) vs. which require both sides changed together
   (e.g. any item touching an API contract).
5. Output the triage plan and stop. Do not write or change any code yet.

## Step 2 — Backend fixes
For items categorized BACKEND:
1. Write/update a test that calls the relevant endpoint (curl or pytest) and
   fails before the fix, passes after. See `CLAUDE.md` for run/test commands.
2. Do not mark complete without the passing test output attached.
// turbo
3. Run the backend locally (`PYTHONPATH=. python3 backend/main.py`) to confirm
   it boots clean, then check for errors/warnings.

## Step 3 — iOS logic fixes
For items categorized iOS-LOGIC:
1. Identify the exact file/struct/function from the mapping above.
2. For re-opened bugs, verify the fix addresses the diagnosed root cause
   (e.g. Codable decoding, NetworkManager caching, UserDefaults persistence)
   rather than reapplying the same patch.
// turbo
3. Build the iOS project (`xcodebuild -project ios/PhoenixCoach/PhoenixCoach.xcodeproj -scheme PhoenixCoach -destination 'platform=iOS Simulator,name=iPhone 16' build`) and resolve all resulting errors/warnings.

## Step 4 — iOS UI/cosmetic fixes
For items categorized iOS-UI:
1. Build and run on Simulator.
2. Capture before/after screenshots using `xcrun simctl io booted screenshot`
   for each affected view — do not use a browser subagent, this app has no
   browser surface. If you create a temporary shell script (e.g., `run_sim.sh`) to automate the build and screenshot process, you MUST delete it from the project root immediately after taking the screenshots to keep the workspace clean.
3. Do not mark complete without an attached screenshot.
4. No logic changes in this step — layout/formatting only (e.g. `DesignSystem.swift`
   tokens, `.lineLimit()`/`.minimumScaleFactor()` for text truncation, `DateFormatter`
   usage from `Formatters.swift` for date format consistency).

## Step 5 — Regression net (only if requested)
For a bug explicitly flagged as recurring (e.g. weekly constraints):
1. Add or extend a test in the backend (or a simple curl check script under
   `scripts/`) that specifically exercises the empty-days case.
2. Set up a scheduled task to rerun it periodically, or note it in
   `scripts/scraper_health_check.py`-style diagnostics if related to data sync.

## Completion criteria
- Backend item → passing curl/pytest test (before/after) + clean local boot
- iOS-logic item → clean Xcode build + stated root-cause diagnosis if re-opened
- iOS-UI item → before/after Simulator screenshot, no logic changes bundled in
