# Running Training — Intensity Bands + Marathon Preparation

<!-- No `## Athlete Context` block here, on purpose. Athlete state is
injected live by `backend/agents/data_agent.py` (actuals from the watch)
and by `periodization_engine.compute_context`. Hand-copying it into this
static corpus produced a coach reading "~20 km/week" and "static bike
only" months after both stopped being true, in the same prompt that
carried the real numbers. Keep this file to method that stays true
regardless of who is reading it. -->

## Race-time conversions — do not do them here

Conversions between distances are computed in
`backend/services/pace_model.py` (Riegel, exponent 1.06), and training paces
come from the watch's measured lactate-threshold pace, not from a race result.
Never quote a VDOT number and never convert a time by hand: a VDOT table that
used to live in this file paired 3:10 with a 1:32:33 half while Riegel gives
1:31:08, and the coach had no way to know which one the app had used.

Two rules that survive from that system and still hold:
- Anchor paces to CURRENT fitness, never to goal race pace.
- Re-check threshold every 4-6 weeks; the watch does this continuously.

### Daniels Training Zones
| Zone | Name | Purpose | % VO2max | Feel |
|---|---|---|---|---|
| E | Easy | Aerobic base, recovery | 65-78% | Conversational, relaxed |
| M | Marathon | Race-specific endurance | 80-84% | Controlled, rhythmic |
| T | Threshold | Improve lactate clearance | 88-92% | "Comfortably hard" — can speak phrases |
| I | Interval | Boost VO2max | 95-100% | Hard — 3-5 min efforts |
| R | Repetition | Speed and running economy | 105%+ | Fast and short — 200-400m with full rest |

### Key Daniels Principles
- 80% of weekly volume should be at Easy pace — most runners go too fast on easy days
- Every run must have a specific purpose — no "junk miles"
- The 2Q (Two Quality) program: 2 hard sessions per week, everything else easy
- Maintain training paces for minimum 3 weeks before adjusting upward
- Quality session volume should not exceed 8-10% of weekly volume per session

## Volume Progression (20 km → 50 km)
- Follow the 10% rule: increase weekly distance by no more than 10% per week
- Use 3:1 build/recovery cycles: 3 weeks building, 1 week reduce by 20-25%
- Example: 20 → 22 → 24 → 19 (deload) → 21 → 23 → 25 → 20 (deload) → ...
- CRITICAL: Previous injury at ~115 km/month — be conservative, prioritize consistency over volume spikes
- If any sharp, localized pain appears: stop and take extra rest immediately
- Cross-training (swim, bike) can supplement aerobic volume without running impact

## Marathon-Specific Training Structure

### Base Phase (8-12 weeks before specific prep)
- 3-4 runs per week
- Long run: 25-30% of weekly volume, at Easy pace
- 1 quality session: tempo (T pace) 20-30 min
- Remaining runs: Easy

### Build Phase (8-12 weeks before race)
- 4-5 runs per week
- Long run building to 28-32 km at Easy pace (include some M-pace segments in final km)
- Quality 1: Tempo — cruise intervals 4-5 x 1 mile at T pace, 1 min rest
- Quality 2: Marathon pace — 10-15 km at M pace within the long run
- 1 easy recovery run

### Peak/Taper (2-3 weeks before race)
- Reduce volume by 30-50%, maintain some intensity touches
- Last hard session: 10-12 days before race
- Race week: very easy short runs, openers 2 days before (4-6 strides)
- Day before: rest or 20 min very easy

## Injury Prevention
- Replace every 500-800 km of shoe usage
- Vary running surfaces when possible (trail, grass, track)
- Dynamic warm-up before every run
- Foam rolling and stretching after runs
- Strength training 2x/week focusing on glutes, core, calves, hamstrings
- If building volume: do NOT also increase intensity simultaneously
