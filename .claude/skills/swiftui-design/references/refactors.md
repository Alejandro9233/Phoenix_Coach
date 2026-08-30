# Known design debt — audit of 2026-08-27

Line numbers are from the audit date and drift with edits; grep the quoted
construct if a line moved. None of this blocks a feature — it's the map,
not a to-do. Per CLAUDE.md, list any drive-by fixes in the pre-commit
summary to Alex — same as feature work.

## Real bugs (fix on sight)

- **White-on-white**: `.foregroundStyle(.white)` on accent fills —
  BlockCalendarView ACTIVE badge (~279-285) and Retry button (~692-702).
  Accent IS white; use `DS.Colors.onAccent`. TodayView's twin errorView
  (~1328) gets the contrast right but via a `.black` literal — tokenize to
  `DS.Colors.onAccent` when copied or touched.
- **Stuck submit state**: all three proposal cards set `isSubmitting = true`
  on Confirm and never reset it; on network failure the card stays mounted
  with both buttons disabled forever (IssueProposalCard ~231,
  RecoveryProposalCard ~113, TravelProposalCard ~107 + CoachChatView error
  paths ~426-517).
- **Invalid SF Symbol** `"trending.up"` renders blank
  (ActivityDetailView ~277). Real name: `chart.line.uptrend.xyaxis`.
- **Fake data styled as live telemetry** in ActivityDetailView: hardcoded
  "PRODUCTIVE", "OPTIMIZED", "AI ENGINE v2.4_ACTVE", "Zone 2 Aerobic",
  "Fully Recovered" (~272-510). Contradicts the app's data-honesty rule.
- **Nil rendered as zero** in ActivityDetailView: `avgHr ?? 0` → "0 bpm",
  "0.0 km", "0%" gauges (~219-358). ActivityCard in FeedbackView does it
  right (conditionally omits).
- **`colorForMuscleGroup` ignores its argument** and always returns
  `outline` (BlockCalendarView ~1273) — white chip text on mid-gray fails
  contrast.
- **Misleading loading state**: typing indicator ("coach is thinking")
  shows while fetching old session history (CoachChatView ~33-35, ~535).
- **Unlocalized display formatters**: `Formatters.dashboardDate` and
  `Formatters.todayDate` (both `"EEEE, MMM d…"`, Formatters.swift ~14-25)
  set no locale — on an es_MX phone they splice "domingo" into English
  copy. Only `yyyyMMdd` sets `en_US_POSIX`; give every display formatter
  an explicit locale. ChatHistoryDrawer also builds new formatter
  instances per row render (~70-89) — use the shared ones.

## Likely dead code (confirm, then delete — don't restyle)

- ConnectionSettingsSheet (TodayView ~1816-1973): orange/system-color alien
  styling, stale "Device Local LLM: Ready" copy (MLX was removed
  2026-07-27), and `showConnectionSettings` is never bound to a sheet.
- ProfileView: `saveSuccess` set but never rendered (~41, ~1053), `isSaving`
  no UI (~39), unused `intBinding`/`doubleBinding` (~1117-1151), unused
  `.serverURL` field case + `backendURLText` (~14, ~61, ~187-192).
- Empty comment husks where style constants were deleted: BlockCalendarView
  ~18-26 ("Quiet Performance" header over blank lines), ProfileView ~6-12,
  ~196-198, FeedbackView ~47-49.
- `Color(hex:)` in DesignSystem.swift is unused by DS itself; its failure
  mode is a silently transparent color. Only real consumers are
  MatrixToggleButton sport hexes and ActivityDetailView's background.
- `showPullDebug` tuning readout still shipped (TodayView ~110-112).

## Extraction candidates (3+ hand-rolled call sites each)

| Construct | Sites | Where |
|---|---|---|
| Uppercase tracked section header | ~20 | RacePlanSheet `header()` (~45) is the closest existing model — bump its 10pt → 11 and add `.textCase(.uppercase)` when converging; SKILL.md's grammar block is the authority, not any call site. TodayView 736/819/877/1004/1537/1590, BlockCalendarView 863/939/1101/1163, ProfileView 204/415/638/852, HistorySheets 51 |
| Capsule chip/badge | ~12 | TodayView 662/1365/1603/1635, BlockCalendarView 279/373/433/605/1171, InjuryLogView statusBadge 214 |
| Black-on-accent capsule CTA | ~7 | TodayView 974/1092/1328/1687, BlockCalendarView 912, HistorySheets 296, proposal-card confirm buttons |
| Hero numeral + unit at baseline | ~8 | TodayView 556/609/671/824, RacePlanSheet 72/124, FeedbackView statView 383, HistorySheets recoveryTile 163 |
| Step timeline dot + connector | 3 | TodayView 1456, BlockCalendarView 1106 (near-verbatim dupes), phase timeline 261 |
| `stepColor(for:)` | 2 verbatim | TodayView 1503, BlockCalendarView 1214 (adds "interval") — one shared helper |
| errorView | 2 | TodayView 1318, BlockCalendarView 682 (one has the white-on-white bug) |
| Inner sub-panel (white .02-.04 fill, r12, .08 stroke) | ~8 | ProfileView 154/335/618, InjuryCard 188, HistorySheets 126/265/507, proposal cards |
| Proposal-card chrome (surface + semantic stroke + actions row) | 3 | Issue/Recovery/TravelProposalCard — identical anatomy, ~30 duplicated lines each |
| Screen background (bg + top radial) | ~5 | TodayView 273, ProfileView 85, FeedbackView 53, HistoryView 19, BlockCalendarView local variant 526 |

## Drift to align opportunistically

- **Double padding on glass cards** (32pt insets): debriefCard, raceWeekCard,
  ProtocolCard, all four RacePlanSheet cards — remove the outer
  `.padding(16)`; `.glassCard()` provides it. MetricChartSheet has the
  same bug spelled as bare `.padding()`, so a `.padding(16)` grep misses it.
- **BlockCalendarView's parallel card system**: every card hand-built as
  surface + r8 + flat hairline, zero `.glassCard()` uses. Align when
  touched.
- **Section-header variants**: 9-12pt, tracking 0.5-3.0, bold/medium —
  converge on the grammar (11 bold, `DS.Tracking.wide`).
- **Springs**: `spring(response: 0.3, dampingFraction: 0.8)` literals ≈
  `DS.Animation.normal` (ProfileView ×8, TodayView, BlockCalendarView);
  off-token one-offs 0.25/0.75, 0.28/0.62, bare `.spring()`.
- **Radii**: literals 4, 6, 8, 12, 16, 20, 24 everywhere; 10 (proposal-card
  buttons ×9) and 24 (GlassDeepCard) have no token — decide, then token.
- **Dismiss idiom**: xmark vs xmark.circle.fill vs three "Done" styles —
  converge on SKILL.md's sheet scaffold (13pt semibold xmark in outline).
- **Page padding per tab**: 16/20/24 — converge on `DS.Spacing.page`.
- **ActivityDetailView**: own background hex `#0e0e10` vs DS background,
  `primaryText` for hero values where every other screen uses white,
  tracking 2.0/3.0 off-scale, `.minimumScaleFactor(0.5)` crutches.
- **Chat tab**: two typography regimes in one tab (fixed sizes in cards,
  Dynamic Type in chat); coach bubble material vs typing indicator surface;
  radii 16/12/20 on peer elements; drawer scrim relies on the implicit
  default `.opacity` transition while the drawer declares
  `.move(edge: .leading)` — make the scrim's fade explicit when touched;
  proposal cards inserted outside `withAnimation`.
- **MatrixToggleButton sport hexes** (#60a5fa/#fb923c/#a78bfa) are the
  app's only per-sport colors — extract one categorical sport-color
  helper. (FitnessChartCard's `Color.blue` is the Fitness metric line,
  FeedbackView ~276 — a separate off-token literal, not a sport color.)
- **Off-scale type sizes** to converge on the SKILL.md scale when touched:
  32 (ActivityCard stats), 20 (RacePlanSheet waypoints), 17-18 (ProfileView
  field values), 9 (fine print several places).

## Structural debt (bigger than a drive-by; propose before doing)

- **`DS.Colors.primaryText` is a near-duplicate of `onSurface`** (0.784 vs
  0.780 gray — visually identical). The app's real hero/title color is
  `.white`. Options: retire `primaryText` (migrate call sites to `.white`
  or `onSurface`), or redefine it as `.white` — the latter visibly
  brightens every screen that uses it today. Decide with Alex, once.

- **Dynamic Type**: ~150 fixed `.font(.system(size:))` call sites, no
  `@ScaledMetric`, fixed frames that break at accessibility sizes (drawer
  width 280, splits columns 44-70pt, picker wheels 110pt). The display
  grammar can migrate to `@ScaledMetric(relativeTo:)` per role.
- **Accessibility**: labels nearly absent app-wide (icon-only toolbar
  buttons, xmark dismissals, charts with no representation, VoiceOver
  reading split tables as fragments); MatrixToggleButton announces the
  sport but not the day — seven indistinguishable "swim enabled" toggles.
- **Contrast**: outline-on-dim stacks (pastWeekCard whole-card .55 ×
  text .5-.7), ActivityDetailView labels at outline .3-.4 (~#3a3a3f on
  #0e0e10), 8-9pt micro text throughout.
- **Snapping state changes**: sync dim (TodayView ~233), error/content
  branch swaps, FeedbackView filter + pagination (with a 300ms sleep
  spinner hack), CircularProgressGauge never animates from 0,
  RunProgressBar never animates, debriefCard declares a transition its
  insertion never triggers.
