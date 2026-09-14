# Today v2 — the decided design (2026-09-12, 13 variants rounds)

Source of truth for the port. Every line below was picked by Alex from a
contact sheet; the losers and why are in SKILL.md's Rejected log. Screens:
`.claude/design-notes/variants/today-round*/` (gitignored). The last built
candidate is `Views/Variants/CurrentVariants.swift` at round 13 in git history
of this session's working tree; the pieces are reusable as-is.

## Screen
- Background: `DS.Colors.background`. The **horizon glow** (white radial,
  radius ~440, opacity ~0.34) rises from under the ring and is the *content's*
  top-aligned background, so it scrolls away with the header (Alex,
  2026-09-13: "top anchored"). Replaces the top radial on this screen only. It breathes (`DS.Animation.ambient`,
  12 s, ±3% opacity, a few points of drift), off under Reduce Motion. The one
  non-state animation in the app, Alex's call 2026-09-13.
- **Film grain** on the screen (fixed, does not scroll): `DS.GrainOverlay(.embers)`
  — ~12k seeded dots denser toward the bottom, plus ~140 brighter specks born
  in the bottom third that climb and fade (12 fps Canvas, frozen under Reduce
  Motion). Grain round of 2026-09-13: still / shimmer / weave / rising / lit /
  flicker → rising; then slow drift / fast drift / embers / scrolling texture /
  breathe → embers.
- No sync pill in the content. Date on the top-left, "data · Sep 12" on the
  top-right (the newest recovery snapshot's date, i.e. when watch data last
  landed — never the device's last fetch), both mono micro-labels. The pull-to-refresh gesture stays; its
  readout during the pull is decided in the port (pill vs the corner label).
- Cards are **outline only**: `RoundedRectangle(DS.Radius.large)` stroke
  white 0.22, no fill, `DS.Spacing.l` padding. Light and grain pass through.
  This is a new card kind; add it to DS as `.outlineCard()`.

## 1. Readiness (hero)
- Open arc, 270°, rotated 135°, diameter 200, split into **four segments**
  (HRV, RHR, form, load — the engine's four checks). A segment is lit when
  its check passed, dimmed (tint at 0.18) when it raised a concern.
- Centre: `Readiness` label · state word 36 ultraLight white · the engine's
  `detail` text 11pt outline, 3 lines max, 156pt wide.
- **Colour rule:** the arc is white on green; `DS.Colors.warning` on yellow;
  `DS.Colors.danger` on red. Nothing else on the screen takes the tint
  except the adapted banner.
- No labels under the arc (removed 2026-09-13; the corner readouts name the signals).
- Corner readouts: bottom-left HRV ms (white) / % vs baseline / RHR + trend;
  bottom-right form (white) / load ratio / ATL · CTL.
- Data: `periodization_engine` recovery check → `status`, `detail`,
  `hrv_vs_baseline`, `rhr_trend`, `tib`, `load_ratio`, plus which concerns
  fired. **Must be exposed by the API** (today only the coach prompt sees it).
- No VO2 max anywhere on Today.

## 2. Today's session (instrument card)
- Header row: sport icon + title 15 semibold; subtitle "Ride · Zone 3 ·
  135–150 bpm" 11 bold uppercase outline.
- Split body: left column hero minutes 36 ultraLight + "target Z3" (zone
  colour) + HR range; 1pt hairline; right column mono step table
  `Z1  WARMUP  15:00`, zone label in zone colour, hairlines between rows.
- Zone segment bar (existing `SessionSegmentBar`).
- **Key set panel** (inner panel, white 0.03, radius medium): `Key set`
  label + `Z3 · 45 min` in zone colour, then the step description in white
  13pt. Key set = the longest step that isn't warm-up or cool-down.
- Rows stay tappable to reveal their description; no chevrons drawn.
- **Coach opener** (last row): sparkles + one line in the coach's voice from
  the plan's `coach_note` ("Questions about the 4×10? Ask me.") + ↗. Tap
  opens the Coach tab with today's session as context, prompt prefilled.
- **Adapted day:** an **orange hairline banner above the card** — rule,
  `ADAPTED` mono + engine reason 11pt, right-aligned "use the original ·
  <title> <min>" mono link, rule. Banner takes the readiness tint (orange on
  yellow, red on red). The card itself does not change. Opener becomes
  "I changed today. Ask me why."
- **Rest day:** card shrinks to bed icon + "Rest day" + "no session" mono,
  one sentence, and the opener "Rest is training. Ask me what counts."
- **Race week:** its own outline card above the session: `Race week` label
  + date, race name, day-count hero 36, 14-segment strip to race day, taper
  note. Shown in the final two weeks.

## 3. This week
- `This week` label, `adherence 82` mono on the right.
- `Sessions` label + six segments (done white, rest white 0.10) + `4 / 6`.
- **Run km keeper row**: label · 3pt bar · `18.4 / 32` mono. (keepers.md)

## 4. Training timeline
- Hairline row: label + 14-tick week strip (done ticks white, current tick
  taller) + chevron; beneath, `Build · week 6 of 14 · race in 8 wk` mono.
  Needs phase + week number on Today (BlockCalendarView already fetches).

## Not designed — decide in the port with a screenshot each
- Pull-to-refresh readout (pill vs corner label), loading skeleton, error +
  retry, offline, debrief card after a refresh, race-setup card, the
  rationale section (opener replaces the note; rationale → chat or drop),
  HRV/RHR/load chart sheets (open from the corner readouts).
