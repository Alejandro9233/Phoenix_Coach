# Backlog

Ideas and known gaps. Not a plan — nothing here is committed to.

## High

- **Activity capture depth** — feed HR zone distribution, GPS, and training load into the
  coaching loop. The LLM currently sees little more than duration and average HR.
- **Scraper monitoring** — COROS breaks silently. A scheduled health check that alerts
  beats finding out via a failed morning refresh.
- **Chat context** — the chat prompt knows less than `/coaching` does. Give it the same
  RAG summaries and recent training state.

## Medium

- **Soreness & injuries into adaptation** — both are logged in the DB but aren't fully
  wired into the adaptation prompt.
- **Test coverage** — chat sessions, context injection, and ingestion are covered.
  `periodization_engine.py` and `plan_normalizer.py`, the highest-risk logic, have none.

## Low

- **Apple Watch / HealthKit import** alongside COROS.
- **Multi-athlete** — athlete ID 1 is hardcoded throughout.
