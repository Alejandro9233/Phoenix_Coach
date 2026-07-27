---
description: For UI/design changes in Phoenix Coach — generates multiple design options via Stitch MCP, presents them for approval, only then implements in SwiftUI.
---

# Design Workflow (Stitch-Assisted)

Use this whenever the task involves a new screen, a redesign, or any UI where
visual/UX judgment matters — not for pure logic fixes or bug patches.

## Step 1 — Clarify the design brief
Before generating anything, restate:
1. Which view is being changed/created (map to the known files: Today =
   `TodayView.swift`, Recent = `FeedbackView.swift`, Profile = `ProfileView.swift`,
   Session Review = `ActivityDetailView.swift`, Journal = `DashboardView.swift`).
2. What existing design constraints apply — pull from `DesignSystem.swift`
   (the `DS` enum: colors, radii, tracking, animations, `GlassPanelCard`/
   `.glassCard()`), since new designs must stay consistent with the existing
   glassmorphism style, not introduce a new visual language.
3. Any functional requirements the design must support (e.g. if this is the
   Journal tab, note it currently has zero content structure to work from).

## Step 2 — Generate options via Stitch MCP
1. Use the Stitch MCP server to generate 3 design variants for the brief.
2. Do not pick one automatically. Present all 3 to me with a one-line
   description of each (layout approach, what it emphasizes).
3. Do not write any SwiftUI code yet.

## Step 3 — Wait for selection
Stop and wait for me to choose a variant, request changes to one, or ask for
a new batch. Do not proceed until I explicitly approve one.

## Step 4 — Translate to SwiftUI
Once a variant is approved:
1. Fetch the Stitch design (HTML/CSS reference) and translate it into SwiftUI,
   reusing `DesignSystem.swift` tokens rather than hardcoding new colors/radii/
   fonts — the Stitch output is a visual reference, not a literal port.
2. Build the project and resolve all errors/warnings.
// turbo
3. Run on Simulator and capture a screenshot with
   `xcrun simctl io booted screenshot` for comparison against the approved
   Stitch variant.
4. Do not mark the task complete without that screenshot attached.

## Hard rule
Never skip Step 2/3 and jump straight to SwiftUI implementation, even for
"small" UI changes — this workflow exists specifically because visual
judgment calls should go through Stitch options + approval, not be decided
by the coding agent alone.
