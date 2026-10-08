# COROS MCP — spike findings (2026-10-07)

COROS ships an official MCP server open to any app (support article "Build on
COROS MCP", repo `coroslab/COROS-MCP`). This is what the spike verified against
Alex's account, side by side with a raw scraper payload from the same morning.
**Decision 2026-10-07.** The code-council (full mode) recommended a shadow
period first. Alex, having read the verdict, chose to go live the same day: **MCP
first, Playwright scraper only as fallback** when the MCP is off, fails, or parses
today only partially. The council's risk for this mode — a wrong number that
parses "fine" — is answered with plausibility bounds, a per-field `partial`
verdict that routes the pull to the scraper, and a `coros_source` record on
every refresh event. See "Live sync" below.

Client: `backend/services/coros_mcp.py` (read-only; refuses create/update/schedule).
CLI: `scripts/coros_mcp_cli.py` (login, whoami, tools, call, gate).
Raw outputs: `samples/coros_mcp/` (gitignored). Token: `~/.phoenix/coros_mcp/<region>/token.json`.

## Auth — verified live

- Gateway `https://mcp.coros.com/mcp` resolves Alex's account to `https://mcpus.coros.com`.
- OAuth 2.0 authorization_code + PKCE S256, dynamic client registration
  (`/connect/register`, `token_endpoint_auth_method: none`), scopes
  `openid offline_access mcp.tools`, `resource` = the MCP URL.
- COROS's own helper documents a headless password path: GET authorize → POST
  the COROS login form → follow redirects to the client callback → exchange the
  code. It worked first try with the scraper's `COROS_EMAIL`/`COROS_PASSWORD`.
  A browser path (CLI login session, print link, poll) is also implemented.
- Access token lives **30 days**; a refresh token is issued. After one
  authorization the backend never needs the password again.
- MCP is stateless streamable HTTP: `initialize` once per process, then
  `tools/list` / `tools/call`. No session id, no `notifications/initialized`.
- Registered client on Alex's COROS account: name "Phoenix Coach".

## Output format — the big caveat

Every tool except `queryActivityLapData` returns **formatted prose**, not JSON.
No `structuredContent`. Example: `Short-Term Load: 52`, `HRV Avg: 71 ms — Below
normal`, `2026-10-07: 50 bpm`. A client has to parse text. The formats are
template-stable within a release, but COROS can reword them without notice, so
any parser needs fixture tests on `samples/coros_mcp/*.json` and a
missing-field alarm like the scraper's `_missing()`.
`queryActivityLapData` returns real JSON (`columns`, `lapGroups[type 10 km
splits / 2 programmed blocks / -1 whole]`) — the same lap encoding
`activity_blocks.py` already decodes.

## Field map — what the scraper ingests vs what MCP exposes

Checked against `analyse_query.dayList`, `dashboard_query.summaryInfo` and the
activity list for the same days.

| DB column (recovery_snapshots) | Scraper field | MCP source | Status |
|---|---|---|---|
| hrv_ms | avgSleepHrv | querySleepHrv "HRV Avg" | match on every day compared |
| hrv_baseline | sleepHrvBase | querySleepHrv "Baseline" | match |
| (new) normal range | sleepHrvIntervalList[2..3] | querySleepHrv "Normal Range" | match; MCP also labels Low/Below/Normal/Above |
| hrv_sd | sleepHrvSd | — | **missing** (nothing reads it) |
| resting_hr | testRhr, else rhr | queryRestingHeartRate | match on every day compared |
| ati | ati | queryTrainingLoadAssessment "Short-Term Load" | match |
| cti | cti | "Long-Term Load" | match |
| load_ratio | trainingLoadRatio | "Load Ratio" | match |
| load_ratio_state | trainingLoadRatioState | derive: fixed zones 0.5/0.8/1.0/1.5/1.8 | 84/84 exact |
| tib | tib | derive: **cti − ati** | 84/84 exact |
| fatigue_pct | tiredRateNew | derive: **−tib** | 84/84 exact |
| fatigue_state | tiredRateStateNew | derive: zone of −tib at −cti, −cti/2, −0.1·cti, 0.05·cti, 0.8·cti | 82/84 (two boundary days) |
| recommend_tl_min / max | recomendTlMin/Max | derive: **7 × cti of the previous Sunday**, max = 1.8 × min | fits all 4 weeks sampled |
| t7d_load, t28d_load | t7d, t28d | — | **missing** (data_agent prose only) |
| recovery_score | summaryInfo.recoveryPct | queryRecoveryStatus "Recovery: N%" | match; adds level + hours to full recovery |
| vo2_max | vo2max | queryFitnessAssessmentOverview | match |
| performance_index | staminaLevel | "Running Level" | match, rounded to an integer |
| performance_score | performance | — | **missing** (data_agent prose only) |
| ltsp | ltsp | "Threshold Pace m:ss" | match (seconds per km) |
| lthr | lthr | — | **missing** — read by personal_model (5×), engine, data_agent |
| training_load (daily) | dayList.trainingLoad | sum of getActivityDetail "Training Load" per activity that day | derivable, one call per activity |
| sleep_duration_hr, sleep_quality_score | never written | querySleepOverview score, main sleep, stages, window, naps | **new — fills the NULL columns** |
| stress_level | never written | queryStressLevel daily average | **new** |
| body_battery | never written | — | stays NULL (COROS has no such metric) |

| athletes.* | Scraper | MCP | Status |
|---|---|---|---|
| weight_kg | profile.weight | queryUserInfo | match |
| hr_zones, pace_zones | lthrZone / ltspZone | — | **missing** — compliance.py and data_agent read them |
| hrv_baseline, vo2_max, threshold_pace_min_km, hr_rest, stamina_level | dayList | as above | covered |
| ftp_watts, max_hr | profile | — | missing (no bike now; max_hr unread) |

| activities.* | Scraper list row | MCP | Status |
|---|---|---|---|
| id (labelId), sport_code | labelId, sportType | querySportRecords | match — same ids, so dedupe is seamless |
| start_time | `timestamp` = **midnight of happenDay** | startTimestamp / endTimestamp (real clock) | **MCP fixes the 00:00:00 bug** |
| duration_sec, distance_m, avg_hr | ✓ | ✓ | covered |
| avg_speed_ms | avgSpeed (s/km) | "Average Pace 7:09 /km" | covered (parse) |
| training_load | trainingLoad | getActivityDetail only | one extra call per new activity |
| cadence, step_count, total_ascent_m, avg_power | pitch, step, totalElevation, avgPower | getActivityDetail (cadence, stride), lap JSON | mostly covered; elevation unverified (indoor runs only this month) |
| sets (strength) | sets | "Sets: 25" | covered |
| sub_mode | subMode | lap JSON `mode`/`subMode` | covered |
| lap_data | detail endpoint via sniffed token | queryActivityLapData | covered, cleaner |
| calories, max_hr, aerobic TE, "Training Focus" | — | getActivityDetail | new |

Also new: FIT download URLs (50/day), daily steps/calories/avg HR, 29-day HRV
assessment history (scraper's `sleepHrvList` carries 7 days).

## Write path — the contract

Tools live since 2026-09-21: `createScheduledWorkout(date, course)`,
`updateScheduledWorkout(date, idInPlan, course)` (full replacement),
`createTrainingPlan` (4–16 natural weeks, start today..+14 d), `scheduleWorkout`
(library template onto a date).

Course shape (verified on Alex's own "Easy run"):
```json
{"courseName": "...", "courseDescription": "non-empty", "sportType": 1,
 "sections": [
   {"sectionType": 1, "targetType": 2, "targetValue": 600,  "intensityType": 1, "sectionIntensity": 1},
   {"sectionType": 2, "targetType": 2, "targetValue": 1800, "intensityType": 1, "sectionIntensity": 2},
   {"sectionType": 4, "targetType": 2, "targetValue": 300,  "intensityType": 1, "sectionIntensity": 2}]}
```
- `sectionType` 1 warmup / 2 training / 3 recovery / 4 cooldown — **identical to
  Phoenix's step `type` vocabulary** (`warmup|main|recovery|cooldown`).
- `targetType` 1 distance (m) / 2 time (s) / 4 free. Phoenix `duration "MM:SS"`
  → type 2; `distance_km` → type 1.
- Intensity, exactly one format per section: `sectionIntensity` zone 1–6 (LTHR
  zones) · percent of threshold · absolute range (`intensityValueStart/End`,
  bpm 30–240 or s/km 120–1499). Phoenix `zone` → `intensityType 1` +
  `sectionIntensity`; `pace_target` → `intensityType 2` absolute range.
- Interval groups: `{"intervalGroup": true, "repeats": n, "sets": [sections of type 2/3]}`.
- `sportType` 1 running / 2 cycling / 5 trail. **No strength, swim, elliptical.**
- Dates: today..+90 d in the COROS profile timezone. Editable only if MCP-expressible,
  description non-empty, not completed. **No delete or move via MCP** (COROS app only).
- Alex already schedules library workouts by hand from the COROS app and follows
  them. The write path automates a step he does today.

## Gaps to decide on

1. **lthr and HR/pace zones are not exposed.** personal_model, compliance and the
   engine read them. Options: keep them as profile fields seeded from the last
   scrape (they move slowly), or let Alex set LTHR in Profile.
2. **t7d/t28d, performance, hrv_sd** disappear. Only data_agent prose reads them.
3. **Prose parsing** — see caveat above. Budget for fixtures and an alarm.
4. **A stripped day can't be deleted on the watch.** If constraint_enforcer or an
   injury removes a run after it was pushed, Phoenix can only overwrite it
   (e.g. a short easy walk with a note) or leave it for Alex to delete in the app.
   Push only after `enforce_constraints`; adapt-today = `updateScheduledWorkout`.
5. **Timezone**: MCP dates follow the COROS profile timezone; Phoenix follows the
   phone. Both are CDMX today; travel is the edge.

## Recommendation

The read side reaches parity on every value the adaptation gates use (HRV,
baseline, RHR, TIB, fatigue state, load ratio, recovery %), with three
arithmetic derivations verified on 84 days of scraper history. The write side maps 1:1 onto
Phoenix's step vocabulary. The costs are a text parser and the lthr/zones gap.
Next step: code-council, full mode (dependency swap + auth surface). Scraper
stays as the live path and as fallback throughout.

## Live sync (MCP first, scraper fallback)

`_pull_coros` in `backend/main.py` runs for both `/smart-refresh` and
`/pull-to-refresh`:

1. If a token file exists on the host and `COROS_MCP_ENABLED` isn't `0`,
   `coros_mcp.fetch_scrape_shaped(days, known_activity_ids)` runs in a thread
   under a 120 s cap: `querySportRecords` for the window (10 days, 90 when the
   DB is shallow), `getActivityDetail` for every activity that is new or from
   the last 2 days, then sleep HRV, resting HR, load, sleep overview, stress,
   fitness, recovery and profile. About 10 calls plus details, a few seconds.
2. `build_scrape_payload` turns the parsed answers into **the scraper's dict
   shape**, so `ingest_coros_data` stays the only writer. Keys the MCP can't
   supply (daily training load, LTHR, zones, t7d/t28d, HRV sd) are simply
   absent and ingestion now leaves those columns alone. New keys:
   `sleepScore`, `sleepDurationMin`, `stressAvg` → `sleep_quality_score`,
   `sleep_duration_hr`, `stress_level`; activities carry `startTimeLocal`
   (athlete's wall clock), `name`, `calories`, `source="coros_mcp"`.
3. `today_status`: `ok` → ingest; `no_today_row` (watch not synced yet) →
   ingest, staleness guard handles the morning; `partial` (today present but a
   gate field unparsable or out of bounds) → **fall back to the scraper**. Any
   exception or timeout also falls back.
4. The refresh event records `coros_source = {source, fallback_reason,
   elapsed_ms, calls, today_status, missing}` (schema_version 3). When a
   morning looks wrong, read that first.

Reading the record (read-only, prod):
```sql
select local_day, payload_json->'coros_source'->>'source' as source,
       payload_json->'coros_source'->>'fallback_reason' as reason,
       payload_json->'coros_source'->>'elapsed_ms' as ms
from refresh_events order by created_at desc limit 30;
```

Arm a host once (same `.env` credentials the scraper uses):
```bash
cd ~/phoenix && ./venv/bin/python3 scripts/coros_mcp_cli.py login && ./venv/bin/python3 scripts/coros_mcp_cli.py whoami
```
`scripts/coros_mcp_cli.py pull` runs the exact fetch the refresh runs and prints
the payload without touching a database. The access token lasts 30 days and
refreshes itself; if refresh fails the event says so and `login` must be run again.

Switches: `COROS_MCP_ENABLED=0` forces the scraper; `COROS_MCP_TOKEN_PATH` /
`COROS_MCP_TOKEN_ROOT` relocate the token. Tests set `COROS_MCP_ENABLED=0`.

Known differences from the scraper path: per-activity lap/block compliance
(`standardRate`) is not in the MCP lap output, so `activity_blocks.py` keeps the
old detail endpoint; HR/pace zones and LTHR keep their last scraped values;
`hrv_sd`, `t7d/t28d`, daily `training_load` on the snapshot stop updating (no
reader uses them).

## Readers to build next (playground 2026-10-07, Alex's order of priority)

Each gives a new field a consumer on day one. Playground raw outputs and INSIGHTS.md live in
`samples/coros_mcp/playground/` (gitignored, personal).

1. **Readiness on COROS's own terms — shipped 2026-10-07 (HRV part).**
   `recovery_snapshots.hrv_normal_low/high` hold COROS's "Normal Range" (MCP
   `querySleepHrv`, or the scraper's `sleepHrvIntervalList[2:4]`). When a row
   has it, the `hrv_drop` gate, the Today readiness check and the LLM alert all
   use "below the band" instead of −15% vs the stored baseline, which had fired
   on 6 of the 7 refreshes after the move. Rows without a band keep the legacy
   rule. Still to do here: sleeping HR (`queryDailyHealthData`) as the
   acclimatization marker.
2. **Weekly volume that shows what disappeared.** Hours per sport per week from
   `querySportRecords` (cycling went to zero after the move with nothing recorded in
   its place; elliptical is never on the watch). Surfaces the gap the engine can't see.
3. **The write path** (`createScheduledWorkout` / `updateScheduledWorkout`), its own
   council. Half of the training days in the sample had nothing on the watch calendar;
   the scheduled ones were followed.
4. **Race predictions on Profile** (`queryFitnessAssessmentOverview`), tracked weekly
   against the goal and the Nov 8 half trial.
5. **Strength exercise log** from `queryActivityLapData` on strength sessions
   (exercise name, reps, time, HR per exercise) for injury-prevention tracking.
6. Later: nightly HRV curve shape (window by the sleep window first; 18% of raw points
   are awake), outdoor FIT running power/dynamics on demand, real start times after
   timezone conversion.

## Watch push (write path) — council 2026-10-07

Full council, verdict "yes with changes" on the athlete's shape (push the week
once at generation, edit the day on adaptation). Decisions:

- **Encoding:** HR zone on every section, Phoenix `zone` n → `sectionIntensity` n
  (Phoenix zones are the athlete's COROS LTHR zones already). `pace_target` goes
  in the description so he can set the treadmill. Pace sections for outdoor quality
  runs are a follow-up. Steps map 1:1 to time-target sections. No interval groups
  (Phoenix steps are a flat list).
- **Stripped day:** overwrite with `Phoenix <slot> · CANCELLED — <enforced_reason>`,
  one free section, description "Don't train this. Delete it in the COROS app."
  Never an "optional easy" run: the enforcer stripped the day because running was
  ruled out.
- **Identifiers:** a small `watch_workouts` table keyed by (date, slot) AND a read of
  `queryTrainingSchedule` before every create. Phoenix names its courses
  `Phoenix <slot> · <title>` so the watch can be matched back. Ids in `plan_json`
  are impossible: `normalize_plan` drops unknown keys and regenerate deletes the row.
  A table alone misses the create-timed-out-but-landed retry; the watch alone
  misses a bad parse. If the schedule read fails to parse, nothing is created.
- **When:** after `db.commit()`, outside `_PLAN_GENERATION_LOCK`, from today
  forward only. Running and cycling only; strength is not pushed in v1 (the
  athlete's library templates have empty descriptions and may never be editable).
- **Safety:** `McpClient.call` stays read-only; a separate write entry allows exactly
  `createScheduledWorkout` and `updateScheduledWorkout`. Env flag `COROS_WATCH_PUSH`,
  default off. Every write leaves a row (status, returned id, error).
- **Mapper refuses rather than guesses:** a step without an integer zone, a
  duration that isn't M:SS, a sport COROS can't create.

Code: `backend/services/coros_watch.py` (pure mapper, placeholder, schedule
parser; tests in `backend/tests/test_coros_watch.py`) and
`backend/services/coros_watch_sync.py` (decisions + idempotent sync + status;
tests in `backend/tests/test_coros_watch_sync.py` against a fake client).

Wiring (`backend/main.py::_watch_sync_safe`, never raises):
- weekly plan generation → push the whole week, after the commit, outside the lock;
- `adapt-today` → update today's course in place;
- `replan-remaining` → update the replanned days;
- morning refresh → backstop reconcile from today forward (covers issue-triage
  writes), report on the refresh event as `watch` (schema_version 4);
- `GET /watch/status` → this week's rows and the newest failure, for Today.

Arming: set `COROS_WATCH_PUSH=1` in the VM's `.env` (needs the MCP token too).
Before that, dry run: `scripts/coros_mcp_cli.py dry-run --plan week.json` prints
each day's course, the watch calendar and the decision (CREATE / UPDATE / SKIP
with reason) and writes nothing. Rollback: unset the flag; existing courses stay
on the watch until deleted in the COROS app.

Known limits in v1: the id returned by create/update is parsed from prose
(`idInPlan: N`); if the format differs the row is marked failed and the next run
adopts the workout by its Phoenix name. The athlete's own hand-scheduled items
block a push for that day (skip, reason recorded) until he removes them.
