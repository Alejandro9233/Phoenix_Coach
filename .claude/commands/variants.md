---
description: Build 5 real SwiftUI variants of one component or screen, screenshot each in the simulator, and hand Alex one numbered contact sheet to judge. Alex is the only judge.
---

# /variants — $ARGUMENTS

Alex judges, Claude builds. Never pick a winner yourself, never port before
he answers, never rank the variants in the message.

## 1. Brief (no code yet)

Load the `swiftui-design` skill. Then restate in 5 lines:
- the component or screen, and the file it lives in
- the data and states it must show (loading / empty / error / content)
- the DS tokens and existing constructs it must reuse (check
  `references/refactors.md` before building any header, chip, CTA, stat tile)
- what `references/taste/` says that applies (read the notes)
- what the **Rejected** log in SKILL.md rules out — do not resubmit a killed direction

## 2. Five variants, five directions

Overwrite `Views/Variants/CurrentVariants.swift` so `view(_ n:)` returns
variant 1–5. Five *directions* (layout, hierarchy, density, what is loud),
not five tweaks of one idea. Every variant obeys the skill and the
design-gate hook; the gate applies to this file too. Feed real-looking data
(same numbers in all five) so Alex compares design, not content. Each
variant is rendered inside the screen scaffold so it is judged in context.

## 3. Build, screenshot, compose

```bash
cd ios/PhoenixCoach
xcodebuild build -project PhoenixCoach.xcodeproj -scheme PhoenixCoach \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -derivedDataPath /tmp/pc-dd
APP=$(find /tmp/pc-dd -name PhoenixCoach.app -path '*iphonesimulator*' | head -1)
xcrun simctl boot "iPhone 17 Pro" 2>/dev/null || true
xcrun simctl install booted "$APP"
S=<scratchpad>/variants; mkdir -p "$S"
for n in 1 2 3 4 5; do
  xcrun simctl terminate booted Alejandro.PhoenixCoach 2>/dev/null || true
  xcrun simctl launch booted Alejandro.PhoenixCoach --variant $n
  sleep 2
  xcrun simctl io booted screenshot "$S/$n.png"
done
swift scripts/contact_sheet.swift "$S/sheet.png" "$S"/1.png "$S"/2.png "$S"/3.png "$S"/4.png "$S"/5.png
```

Look at `sheet.png` yourself first. A variant that clipped, overflowed, or
rendered blank gets fixed and re-shot before Alex sees it. For motion,
record each variant instead:
`xcrun simctl io booted recordVideo --codec h264 "$S/$n.mp4"` (stop with
ctrl-c), and send the five clips.

## 4. Hand it over and stop

Send `sheet.png` with SendUserFile. In chat: one line per variant, what the
direction is, no adjectives, no recommendation. Ask for a number. End the
turn. Alex may answer with a number, "none", or "3 but with 5's header".

## 5. After the pick

- Port the winner into the real view with DS tokens. Extract any construct
  used twice into DesignSystem.swift.
- `git checkout -- ios/PhoenixCoach/PhoenixCoach/Views/Variants/CurrentVariants.swift`
- Append one line per loser to the **Rejected** log in
  `.claude/skills/swiftui-design/SKILL.md`: date, what, and why if Alex said
  why. If he said "none", log all five and ask what was missing before
  building the next round.
- If the same component comes back for a second round, the skill is
  underspecified. Fix the rule first, then build.
