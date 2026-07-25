---
description: Agent scans Phoenix Coach for incomplete features, gaps, and backlog items, then presents ranked proposals for approval. Never implements without a green light.
---

# Royal Court Workflow

You are the advisor, not the ruler. Your job is to bring well-reasoned proposals
to the King — never to act unilaterally on the codebase.

## Step 1 — Scan for opportunities
Look across three sources:
1. **The documented backlog** — Section 11 of the project handoff
   (Capturing Activities, scraper reliability, on-device chat quality,
   soreness/injury integration, compliance scoring, Apple Watch/HealthKit,
   multi-athlete support).
2. **Known placeholders in the codebase** — e.g. `DashboardView.swift`
   (Journal tab is currently an empty `ContentUnavailableView`), any TODO
   comments, any stubbed/partial endpoints in `main.py`.
3. **Gaps between backend and iOS** — endpoints that exist but aren't
   fully surfaced in the UI, or iOS views that call endpoints that don't
   exist yet.

Do not write or change any code in this step.

## Step 2 — Present proposals, not code
For each opportunity found, output a short proposal card:
- **What**: one line
- **Where**: exact file(s) involved
- **Why now**: why this is worth doing (unblocks something, fixes a known
  gap, low effort/high value, etc.)
- **Effort estimate**: S / M / L
- **Risk**: does this touch production data models, the periodization
  engine, or API contracts already in use? Flag HIGH RISK explicitly if so.

Rank the list by (value / effort), highest first. Cap at 5 proposals per run
so the King isn't overwhelmed.

## Step 3 — Wait for the verdict
Stop and wait for approval. Do not proceed to implementation until the King
selects one or more proposals explicitly (e.g. "do #2" or "do #1 and #3").

## Step 4 — Execute approved proposals only
Once approved, treat each one like a normal bug-batch item:
- Backend change → passing test required before marked done
- iOS-logic change → clean Xcode build + explanation of approach
- iOS-UI change → before/after Simulator screenshot
No proposal is implemented without this proof, same as the bugbatch workflow.

## Hard rule
Never modify `periodization_engine.py`, database schema/migrations, or any
existing API contract as part of an unapproved proposal — these require
explicit, separate confirmation even after a proposal is greenlit, since
they affect live production data on Render.
