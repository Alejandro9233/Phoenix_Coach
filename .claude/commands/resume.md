---
description: Resume in-flight work from the restart brief, verifying its claims before acting and reading only the files actually in play
---

# Resume from the restart brief

Run this as the FIRST message in a fresh conversation, to pick up where the last
session stopped without re-reading the codebase.

## Step 1 — Read the brief

Read `.claude/restart-brief.md`.

If it doesn't exist, say so and ask what to work on. Do not go exploring the repo
to guess.

## Step 2 — Check whether it went stale

```bash
git rev-parse --short HEAD
git status --short
```

Compare to the `HEAD at write time` recorded in the brief:

- **Same SHA, same dirty files** → the brief is current. Trust its mechanical half.
- **Different SHA** → commits landed since it was written. The "Done" and "Next"
  sections may already be obsolete. Say this out loud and re-verify before acting.
- **Working tree differs from the recorded `git status`** → files changed outside
  the brief's knowledge. Treat all its claims as suspect.

## Step 3 — Verify before trusting

Everything under `## Unverified claims` — and every line number anywhere in the
brief — is a **lead, not a fact**. Confirm with a targeted grep before acting on it.

A stale line number that gets trusted produces a confident, wrong edit. That failure
is silent and expensive. A grep costs ~50 tokens. Always pay the grep.

## Step 4 — Read narrowly

Read **only** the paths under `## Files in play`. Nothing else.

Do not "get oriented" by reading the tree, the endpoint list, or neighboring modules.
Orientation is already covered:
- `CLAUDE.md` loads automatically — rules, gotchas, and where things live
- Module docstrings carry the "why" (`periodization_engine.py`, `plan_normalizer.py`)
- `docs/DEPLOY.md` — only when deploying

Broad re-reading is exactly the cost this system exists to avoid.

## Step 5 — Confirm, then work

State in 3 lines:
1. The task, as you now understand it
2. Anything in the brief that no longer holds, and why
3. The next concrete action

Then proceed. Respect the `## Ruled out` section — don't re-propose rejected
approaches unless the reason recorded there has actually stopped applying, and say
so explicitly if you think it has.
