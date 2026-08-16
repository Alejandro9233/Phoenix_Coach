# Phoenix Project — Standing Rules

## Project context
- Core logic: Python (FastAPI) for the backend, iOS (SwiftUI) for the frontend mobile app (`PhoenixCoach`).
- Local dev: backend runs at `http://localhost:8001` (typically `PYTHONPATH=. ./venv/bin/python3 backend/main.py`).
- Project root: `/Users/alex/Documents/Code/Phoenix_Project`

## Environment
- All Python commands (`pytest`, `uvicorn`, `python`) require the virtualenv. Either activate it first (`source /Users/alex/Documents/Code/Phoenix_Project/venv/bin/activate`) or use the explicit path (`venv/bin/python`).

## Testing discipline (applies to ALL code changes)
- Before considering any logic change complete, relevant tests must pass:
  - Backend / API layer changes → run `PYTHONPATH=. venv/bin/pytest backend/tests/` from the project root and show passing output.
- If tests fail, iterate and fix automatically — don't stop and ask permission to keep going.
- Never mark a task "done" without attaching the actual test output. A description of what should pass is not sufficient.

## Incremental delivery
- Never generate a whole feature or the whole project in a single response.
- Scope each change to the specific module/phase/file actually being worked on. Don't ripple edits into unrelated files "while you're in there" without flagging it first.

## UI / visual changes
- Any new screen, redesign, or change involving visual/UX judgment on the iOS app should go through the `/design` workflow (Stitch MCP → user approval → then implement).
- Never implement major UI changes directly from a text description — route through `/design` first. Pure logic fixes / bug patches with no visual component are exempt.

## Bug reports & QA output
- Any bug report or QA summary the agent produces should follow this shape:
  - Group findings by phase/suite/area, not chronologically.
  - For each failure: exact repro steps, what happened, what was expected.
  - Attach screenshots/video for any failed UI state (e.g., from the iOS Simulator) — don't just describe it.
- This format applies whether it's a full `/bugbatch` run or an ad-hoc bug found mid-task.

## Housekeeping
- If you create a temporary script to automate a build/test/screenshot step, delete it once you're done using it. Don't leave scratch scripts in the project root.
- Keep configuration files (like `Procfile` or `requirements.txt`) in sync if the project structure or start commands change.
