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
- **Nature**: VISUAL (new/changed UI, needs design judgment) or
  FIX-OR-BACKEND (logic, bug, backend-only, no design judgment needed)
- **Risk**: does this touch production data models, the periodization
  engine, or API contracts already in use? Flag HIGH RISK explicitly if so.

Rank the list by (value / effort), highest first. Cap at 5 proposals per run
so the King isn't overwhelmed.

## Step 3 — Wait for the verdict
Stop and wait for approval. Do not proceed to implementation until the King
selects one or more proposals explicitly (e.g. "do #2" or "do #1 and #3").

## Step 4 — Auto-route to the right workflow
Once one or more proposals are approved, route automatically without asking
which workflow to use:
- If a proposal's Nature is VISUAL → call /design with that proposal as the
  brief. Do not write SwiftUI directly — let /design generate Stitch options
  first and wait for a variant to be approved before implementing.
- If a proposal's Nature is FIX-OR-BACKEND → call /bugbatch with that
  proposal reframed as a bug/change item, following its triage → fix →
  verify steps.
- If a proposal has both a backend and a visual component, call /bugbatch
  first for the backend/API part, then /design for the UI part once the
  backend is verified working.

Multiple approved proposals may be routed in parallel via subagents if they
don't touch shared files.

## Hard rule
Never modify `periodization_engine.py`, database schema/migrations, or any
existing API contract as part of an unapproved proposal — these require
explicit, separate confirmation even after a proposal is greenlit, since
they affect live production data on Render. This rule applies regardless of
which downstream workflow (/design or /bugbatch) ends up executing the work.