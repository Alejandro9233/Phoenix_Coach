# Cycling Training — HR-Based (No Power Meter)

<!-- No `## Athlete Context` block here, on purpose. Athlete state is
injected live by `backend/agents/data_agent.py` (actuals from the watch)
and by `periodization_engine.compute_context`. Hand-copying it into this
static corpus produced a coach reading "~20 km/week" and "static bike
only" months after both stopped being true, in the same prompt that
carried the real numbers. Keep this file to method that stays true
regardless of who is reading it. -->

Road bike, outdoor. No power meter, so every zone below is heart rate. An
indoor trainer is a substitute for weather, not the default.

## Heart Rate Zones for Cycling (Friel 7-Zone, Based on LTHR)

### How to Determine LTHR
- Best method: 30-minute time trial on the bike, average HR of last 20 minutes = LTHR
- Alternative: Use the value from COROS EvoLab (currently 177 bpm)
- Re-test every 8-12 weeks

### Zone Definitions (Based on LTHR of 177 bpm)
| Zone | Name | % of LTHR | HR Range (bpm) | Purpose | Feel |
|---|---|---|---|---|---|
| Z1 | Recovery | <81% | <143 | Active recovery | Very easy, barely feel it |
| Z2 | Endurance | 81-89% | 143-157 | Aerobic base building | Comfortable, conversational |
| Z3 | Tempo | 90-93% | 159-165 | Sustained aerobic power | "Comfortably hard" |
| Z4 | Sub-Threshold | 94-99% | 166-175 | Race pace fitness | Hard, short phrases only |
| Z5a | Threshold | 100-102% | 176-180 | At lactate threshold | Very hard, can sustain 20-30 min |
| Z5b | Anaerobic | 103-106% | 181-188 | Above threshold | Extremely hard, 3-8 min |
| Z5c | VO2max | >106% | >188 | Maximum aerobic power | Max effort, 1-3 min |

Note: Zones recalculate automatically if LTHR changes.

## The bike inside a marathon block

Seven randomised trials found no significant difference in VO2max or race time
when 20-50% of running was swapped for cycling. But running economy is
running-specific and does not transfer: over-relying on the bike costs an
estimated 3-5% of economy, which is 8-15 minutes over a marathon. So the bike
buys aerobic fitness, not marathon legs.

What follows from that:

- ADD, don't swap. Substitution earns its keep around 80 km/week and above,
  where there is running to spare. Below that the bike goes on top of the runs.
- Never substitute the long run, marathon-pace work, or threshold running.
  Tolerance to impact only comes from impact. The bike takes easy days and
  recovery days.
- Converting a run to a ride: roughly 1.5:1 by TIME, about 3x the distance.
- Long-run cross-training ceiling is about 15-20% of total volume before
  measurable loss.
- The long RIDE goes the day AFTER the long run, never before it. It flushes
  the legs without adding impact. Hold it strictly in Z2 and use it to practise
  race fuelling (see `knowledge/nutrition.md`). Shorten it when the long run
  passes 30 km.
- Any easy run that starts a niggle becomes a 60-75 min spin the same day. That
  is a substitution rule, not a rest rule — the aerobic hours still happen.

Expect heart rate to sit 5-10 beats lower on the bike at the same perceived
effort. If HR refuses to rise on an easy ride, that is the correct outcome, not
a problem to fix.

## Cycling Workouts

### Endurance Ride (Foundation)
- Duration: 60-120 min
- Intensity: Z2 (143-157 bpm)
- Purpose: Build aerobic base, fat oxidation
- When: 1-2x/week, especially early in training
- Outdoors, pick rolling terrain over stop-start traffic — interruptions break Z2

### Sweet Spot Intervals
- Warm-up: 10 min Z1-Z2
- Main: 3 x 15 min at high Z3/low Z4 (162-170 bpm), 5 min Z1 recovery
- Cool-down: 10 min Z1
- Purpose: Maximum aerobic benefit per unit of fatigue
- When: Build phase, 1x/week

### Threshold Intervals
- Warm-up: 15 min progressive Z1→Z2
- Main: 3 x 10 min at Z4 (166-175 bpm), 5 min Z2 recovery
- Cool-down: 10 min Z1
- Purpose: Raise lactate threshold
- When: Build/peak phase, 1x/week, NOT when fatigued

### High-Intensity Intervals (VO2max)
- Warm-up: 15 min Z1-Z2
- Main: 5 x 4 min at Z5b (181-188 bpm), 4 min Z1 recovery
- Cool-down: 10 min Z1
- Purpose: Improve maximum oxygen uptake
- When: Peak phase only, well-recovered

### Sprint Intervals (Neuromuscular)
- Warm-up: 15 min Z1-Z2
- Main: 8 x 30 sec MAX effort, 2 min easy recovery
- Cool-down: 10 min Z1
- Purpose: Leg speed, power development, neuromuscular activation
- When: Supplement to endurance rides, 1x/2 weeks

## Indoor Trainer — when weather forces it

### Indoor Training Advantages
- Consistent environment — no traffic, weather, or terrain variables
- Perfect for structured intervals — you can hit exact HR targets
- Time-efficient — no travel time, no coasting

### Indoor Training Limitations
- No bike handling skills development
- Heat buildup (use a fan!)
- Mental monotony on long rides
- No drafting/pack riding experience
- Outdoors is the default; the trainer is for weather, darkness or an unsafe road

### Building Toward 90 km Race
- 70.3 bike leg takes 2.5-3.5 hours for most athletes
- Build longest ride gradually: start at 60 min, add 10-15 min/week
- Peak long ride: 2.5-3 hours (8-10 weeks before race)
- Long rides at Z2 (143-157 bpm) — resist going harder
- Practice nutrition on the bike during rides >90 min

## Weekly Cycling Structure

### Base Phase (3x/week)
- 1 long ride (Z2): 60-120 min, building
- 1 sweet spot session: 60-75 min total
- 1 easy recovery ride: 30-45 min Z1

### Build Phase (3-4x/week)
- 1 long ride (Z2): 90-150 min, building to 2.5h
- 1 threshold session: 60-75 min total
- 1 sweet spot or VO2max intervals: 60 min
- 1 recovery ride: 30 min Z1

### Race Prep Phase
- Brick sessions (bike → run): 60-90 min bike Z2-Z3, immediately run 15-25 min Z2
- Reduce volume in final 2 weeks, maintain Z3-Z4 touches
