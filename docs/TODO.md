# Backlog

Ideas and known gaps. Not a plan — nothing here is committed to.

## High

- **Activity capture depth** — feed HR zone distribution, GPS, and training load into the
  coaching loop. The LLM currently sees little more than duration and average HR.
- **Scraper monitoring** — COROS breaks silently. A scheduled health check that alerts
  beats finding out via a failed morning refresh.

## Medium

- **Test coverage** — `plan_normalizer.py` has no test file of its own; it is only
  exercised through the other suites.

## Low

- **Apple Watch / HealthKit import** alongside COROS.
- **Multi-athlete** — athlete ID 1 is hardcoded throughout.
