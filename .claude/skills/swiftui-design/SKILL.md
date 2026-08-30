---
name: swiftui-design
description: Load BEFORE writing or editing any SwiftUI view — any .swift file under ios/PhoenixCoach, the Today/Coach/Recent/Profile tabs, or any iOS screen, card, sheet, chart, animation, button, or styling tweak. Encodes the app's dark "quiet performance" design language - tokens, type grammar, the one card system, sheet and motion vocabulary. Not needed for backend/Python work.
---

# Phoenix Coach SwiftUI design

The app is a dark instrument panel: near-black background, white as the only
accent, glass cards, thin numerals for data, bold tracked micro-labels for
chrome. Color is reserved for meaning (status, zones). Data is loud, chrome is
quiet. Your job when touching UI: extend this language, never invent a new one.

**Read `ios/PhoenixCoach/PhoenixCoach/DesignSystem.swift` before styling
anything.** Every constant you need is a `DS` token. A styling literal
(`.spring(response:...)`, `cornerRadius: 12`, `.tracking(1.2)`,
`Color.white.opacity(...)` for text) is a bug even when it looks right —
four header styles drifted across five files because each was hand-typed.
If a token is missing, add it to `DS` first, then use it.

## The grammar

Every screen and component is built from these roles. Match them exactly.

**Screen scaffold**
```swift
NavigationStack {
    ZStack {
        DS.Colors.background.ignoresSafeArea()
        RadialGradient(colors: [DS.Colors.accent.opacity(0.12), .clear],
                       center: .top, startRadius: 0, endRadius: 400)
            .ignoresSafeArea()
        ScrollView(showsIndicators: false) {
            VStack(spacing: DS.Spacing.section) { ... }
                .padding(.horizontal, DS.Spacing.page)
        }
    }
    .navigationTitle("")
    .navigationBarTitleDisplayMode(.inline)
    // Tab roots hide the system title and draw their own header in content.
    // Pushed screens and sheets use a real title, always .inline.
    // Never a system large title anywhere.
}
```

**Sheet scaffold** — RacePlanSheet is the model for detail sheets:
```swift
NavigationStack {
    ZStack { DS.Colors.background.ignoresSafeArea(); ScrollView { ... } }
        .navigationTitle("Race Plan")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(DS.Colors.outline)
                }
                .accessibilityLabel("Close")
            }
        }
}
.presentationDetents([.fraction(0.88), .large])
.presentationDragIndicator(.visible)
```
Utility input sheets use `Form` + `.scrollContentBackground(.hidden)` +
`.listRowBackground(DS.Colors.surface)` per row, with Cancel (outline) /
confirm toolbar text buttons instead of xmark (AddInjurySheet is the
model). Never ship a `Form` without `.scrollContentBackground(.hidden)` —
the system grouped gray is alien chrome here.

**Cards** — `.glassCard()` is the only card. Never hand-roll
`DS.Colors.surface` + stroke (BlockCalendarView does — drift, don't copy
it). Two rules that keep getting broken:
- `.glassCard()` already contains `.padding(16)`. Adding your own padding
  before it produces 32pt insets — a recurring bug; instances in
  [references/refactors.md](references/refactors.md). Content goes
  straight into the modifier.
- Depth is two levels only: glass card on background, and inside a card an
  inner panel of `Color.white.opacity(0.03)` + `DS.Radius.medium`. Never
  card-in-card-in-card, never shadows — on this background depth comes
  from material + hairline, shadows just muddy.

**Section header** — one construct, everywhere:
```swift
Text("This Week")
    .font(.system(size: 11, weight: .bold))
    .textCase(.uppercase)
    .tracking(DS.Tracking.wide)
    .foregroundStyle(DS.Colors.outline)
```

**Hero numeral** — data wears thin weights, labels wear bold. Changing
numbers must not jiggle or hard-swap:
```swift
HStack(alignment: .firstTextBaseline, spacing: 2) {
    Text("42.5")
        .font(.system(size: 36, weight: .ultraLight))
        .monospacedDigit()
        .contentTransition(.numericText(value: km))   // when the value updates
        .foregroundStyle(.white)
    Text("km")
        .font(.system(size: 13))
        .foregroundStyle(DS.Colors.outline)
}
```

**Chips / badges** — capsule, 10pt bold uppercase,
`.tracking(DS.Tracking.normal)`, `.padding(.horizontal, DS.Spacing.s)`
`.padding(.vertical, DS.Spacing.xs)`. Status: `color.opacity(0.2)` fill +
the same color as text (InjuryLogView's statusBadge is the model).
Emphasis: solid accent fill + `DS.Colors.onAccent` text.

**Buttons** — primary CTA is `DS.Colors.onAccent` (black) text on solid
accent capsule. Secondary is `Color.white.opacity(0.06)` fill +
`.white.opacity(0.12)` hairline. **The accent is pure white: `.white` text
or `.tint(.white)` spinners on an accent fill are invisible.** On any
accent surface, everything on top is `DS.Colors.onAccent`.

**Text input** — `.textFieldStyle(.plain)`, `.padding(DS.Spacing.m)`,
`DS.Colors.surface` fill, clipped to `DS.Radius.xl` for a chat-style bar
(CoachChatView is the model) or `DS.Radius.medium` inside cards; value
text `.white`. Inside a Form sheet, style rows with
`.listRowBackground(DS.Colors.surface)` instead. Never default bordered
field styling — it reads as foreign chrome.

**Text hierarchy** — hero values and card titles are `.white` (white IS
the accent); body is `DS.Colors.onSurface`; labels and hints are
`DS.Colors.outline`. `DS.Colors.primaryText` is a legacy near-duplicate of
`onSurface` (1% apart) — don't use it in new code; migrate to `.white` or
`onSurface` when touched. Never stack `.opacity()` on `outline` — it's
already 57% gray, and opacity chains three multipliers deep exist in the
codebase and are unreadable.

## Typography

- Prose (coach notes, chat, empty states) uses Dynamic Type styles:
  `.subheadline`, `.footnote`, `.caption`. Data display uses the fixed
  grammar. Don't mix regimes inside one card.
- The scale: 48 display (max one per screen) / 36 hero / 24 screen title /
  22 sub-hero / 15 title / 13 body / 11 label / 10 chip. No new
  `.font(.system(size: N))` values outside it; 8–9pt is below the
  legibility floor. Off-scale sizes you'll see (32, 20, 17, 9) are
  converge-on-touch drift, not license.
- A single card gets at most 4 sizes. Hierarchy comes from weight and
  color first, size second. If two sizes differ by 1pt, make them the same.
- `.textCase(.uppercase)` for uppercase, not `"\(x.uppercased())"`.
- Tables of figures (splits, laps): `.monospacedDigit()`, right-aligned,
  never fixed-width `.frame` columns that truncate.
- Every `DateFormatter` / `Date.FormatStyle` sets an explicit locale. Plan
  day keys: `en_US_POSIX` always (CLAUDE.md). Display strings too — the
  app's copy is English, and an unlocalized `EEEE` on the athlete's es_MX
  phone splices "domingo" into English sentences.

## Spacing

`DS.Spacing` only, on the 4/8 grid: `xs 4 / s 8 / m 12 / l 16 / xl 20 /
xxl 24`, plus `page` (16, screen edges) and `section` (24, between cards).
Always pass explicit `spacing:` and `alignment:` to stacks — default
spacing is an accident, not a decision. A card's inner spacing (`m`) must
be smaller than the gap between cards (`section`), or grouping dissolves.

## Motion

`DS.Animation` tokens only: `.quick` (expand/collapse, press feedback),
`.normal` (most state changes, sheet content), `.slow` (rare, large
moves). The hand-rolled `spring(response: 0.3, dampingFraction: 0.8)`
scattered through the app IS `DS.Animation.normal` — reference it.

- **Animate by frequency.** Every-refresh paths (Today sync, tab switches)
  get no animation — a haptic acknowledges instead. Occasional events
  (sheet present, proposal card, plan change) get `.normal`. Rare moments
  (race-week arrival, PR) may get one deliberate flourish.
- **Nothing pops.** Any view that appears or disappears on a state change
  needs a `.transition` and its state mutation inside `withAnimation`.
  Insertions arriving from async work (SSE events, network completions)
  are the repeat offender. Enter at
  `.transition(.scale(scale: 0.96).combined(with: .opacity))` — never
  scale from 0.
- Scope implicit animation: `.animation(DS.Animation.quick, value: flag)`,
  never an un-scoped `.animation` on a container.
- Respect Reduce Motion: `@Environment(\.accessibilityReduceMotion)` —
  swap moves/scales for `.opacity`, keep fades.
- Haptics on meaning, not on taps: new code uses
  `.sensoryFeedback(.success, trigger:)` / `.impact(weight: .light)` at
  state crossings (sync landed, plan applied, gesture armed). One per
  event; none on navigation.
- **Pull-to-refresh**: `.refreshable` is the default for list screens
  (HistoryView). TodayView is the deliberate exception — its two-tier
  hold-to-confirm pull replaced `.refreshable` on purpose; never re-add
  `.refreshable` there, and never copy the custom pull machinery to a
  screen without TodayView's accidental-trigger problem. Read the comment
  block at the top of TodayView before touching any pull logic.

## Charts (Swift Charts)

- Line charts: `.interpolationMethod(.catmullRom)` + an `AreaMark` under
  the line filled with a vertical gradient of the same color fading to
  clear. One accent per chart; a second series is a neutral gray, not a
  new hue — unless each series is itself semantic (FitnessChartCard's
  fitness/fatigue/form is the sanctioned example; its raw `Color.blue`
  line is drift, not license).
- Bars: small top-corner radius (3–5pt); selection = full accent, rest =
  `accent.opacity(0.3)`. No rainbow palettes — semantic colors only where
  color IS the meaning (zones, compliance).
- Strip junk: `.chartLegend(.hidden)` for single series, grid lines at
  `.white.opacity(0.06)`, axis labels `.caption2` + `DS.Colors.outline`,
  explicit `.chartYScale(domain:)` with ~20% headroom so weekly noise
  isn't exaggerated.

## States

Every screen designs four: loading, empty, error, content.
- Loading: `.redacted(reason: .placeholder)` over the real layout — not a
  spinner on blank. This matters here: Render cold starts take 30–60s and
  the skeleton is what makes that survivable.
- Empty: an invitation with a verb (statusCard / `ContentUnavailableView`),
  not mood text.
- Error: message + Retry with a ≥44pt target. A submitting flag must reset
  on failure — a card must never stay disabled after an error.
- Data honesty: a nil stat is omitted, never rendered as `?? 0`. The app
  shows real numbers or nothing.

## Accessibility floor (non-negotiable on new views)

- Tap targets ≥ 44pt: pad small glyphs, then `.contentShape(Rectangle())`.
- Icon-only buttons get `.accessibilityLabel`.
- Stat cards: `.accessibilityElement(children: .combine)`.
- Expandable rows expose state (`.accessibilityValue`, `.isSelected`).
- Color-only meaning (status dots, capsule tints) is duplicated in text.

## Hard bans

- New hues or hex colors. The palette is DS + semantic
  success/warning/danger. Sport/step categorical colors live in one shared
  helper, not per-view.
- Gradients, except: the screen's top radial glow, chart area fills, and
  the glass card's hairline stroke. No gradient buttons, text, or washes.
- Emoji as icons. SF Symbols, `.symbolRenderingMode(.hierarchical)`, one
  weight per screen. Verify names — an invalid symbol renders as blank.
- Default `List` / `Form` styling on designed screens. `ScrollView` +
  `LazyVStack` with the grammar. (`Form` is fine for utility input sheets,
  styled per the sheet scaffold.)
- `UIScreen.main.bounds` → `.containerRelativeFrame(.horizontal)`.
- Deprecated styling: `.foregroundColor`, `.cornerRadius()` →
  `.foregroundStyle`, `.clipShape(.rect(cornerRadius:))`.
- Light-mode work. The app is dark-only by decision
  (`.preferredColorScheme(.dark)` at root). Don't add adaptive system
  colors (`.primary`, `Color(.systemGray5)`) to designed screens — they
  only cohere because dark is forced, and they read as foreign chrome.
- iOS 26 Liquid Glass `glassEffect` on content cards. Accept what the SDK
  gives system bars; content stays opaque dark glass-card language.

## Process

- New construct used twice → extract it into DesignSystem.swift (the
  statusCard scaffold in TodayView documents why: "so their styles can't
  drift"). Candidates already at 3+ call sites are listed in
  [references/refactors.md](references/refactors.md) — check it before
  building any header, chip, CTA, stat tile, or step timeline; one
  probably exists.
- Touching a file = leave it more on-grammar than you found it
  (double-padding, literal springs, `.uppercased()`, off-token colors),
  but don't restyle unrelated screens in a feature commit, and list every
  drive-by fix in the pre-commit summary to Alex — same as feature work.
- Before finishing any UI change, check: tokens only · nothing pops ·
  numerals monospaced · on-accent is black · 44pt targets · four states ·
  builds against iPhone 17 Pro simulator.
