"""Auto-generated metric audit — 3-agent adversarial review of how each metric is
calculated and how it can be cheated / be wrong. Regenerate via the metric-audit workflow.
`mitigated` is set when a fix has since been shipped."""

AUDIT = {
 "generated": "2026-06-20",
 "metrics": [
  {
   "id": "accel_brake_g",
   "name": "Acceleration / Braking G (from speed)",
   "level": 1,
   "cheat": 1,
   "wrong": 3,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "No outlier/accel cap: unlike max_speed (which clamps with max_accel to reject freespin/spikes), _speed_g has zero spike rejection and literally rewards the largest dv/dt, so a single glitchy speed sample directly inflates the score.",
    "Endpoint difference, not an average: g = (v[right]-v[left])/dt over the window uses raw endpoints, so a one-sample sensor jump (e.g. 0->40 km/h in 0.2s ~= 5.7g) is reported as a real hold even though the '1s window' comment implies smoothing.",
    "Freespin feeds it both ways: a lifted/spun-up wheel reads high speed (GPS ~0 only lowers corrob if GPS exists); the spin-up inflates accel_g and the moment the wheel is set down / sensor recovers the speed collapse inflates brake_g.",
    "No GPS corroboration required: _corrob_speed falls back to pure wheel speed when GPS is absent, so wheel-sensor noise or stationary wheel-spin is taken at face value.",
    "Unbanded version counts windows starting from a standstill (0 km/h), so a parking-lot stunt or low-speed launch glitch qualifies; only the separate _speed_g_band (accel_g_30/50) requires real speed.",
    "Gate (GATE_TIERS) only enforces min trip seconds/km, never a minimum speed or plausibility for this metric, so a long slow ride containing one bad sample still posts a top g."
   ],
   "fixes": [
    "Apply the same acceleration-plausibility clamp used by max_speed (reject dv/dt beyond a believable physical bound, e.g. >max_accel), average over the window instead of endpoint-differencing, require >=2 samples spanning ~>=0.5s within the window, and add a minimum-speed/plausibility gate plus GPS corroboration so spikes, freespin and stationary spin can't post a top g.",
    "Require GPS-corroborated speed (drop wheel-only fallback for this board), apply the existing freespin/accel-cap spike filter to the speed track first, fix the sliding window to a true >=~1s average, gate it behind a min-distance/min-speed qualifier like the banded boards, and clamp implausible per-window g (e.g. > ~0.7g) as invalid.",
    "Apply the same max_accel plausibility clamp used by _speeds() (reject/cap deltas implying >~20 km/h/s as freespin/spike), enforce a minimum effective window (skip pairs with dt below ~0.7s and require >=2 corroborated points spanning the window), and add a speed floor so stationary/near-zero spins can't register; ideally require GPS corroboration of the speed change."
   ],
   "hows": [
    "_speed_g takes the corroborated wheel/GPS speed (min of the two, or wheel-only if no GPS) and over a ~1s sliding window computes |dv/dt| converted to g; the largest positive change is accel_g, the largest negative is brake_g. These feed the gated 'Accel G'/'Braking G' boards, gated only by trip duration/distance, not by speed.",
    "Walks corroborated speed samples with a so-called 1s sliding window and reports the single largest rising speed-delta (accel_g) and falling speed-delta (brake_g), converted km/h-per-s to g; it is the max instantaneous slope over the run, not a sustained average like the IMU g boards.",
    "Takes the max change in corroborated wheel/GPS speed across a sliding window (capped at ~1s but always keeping >=2 points) and converts km/h-per-second into g, reporting the strongest speed-up as accel_g and the strongest slow-down as brake_g. Fed to gated leaderboards requiring trip duration/distance minimums."
   ],
   "reviews": 3,
   "mitigated": "Speed-corroborated / interpolated — can't be faked while stationary."
  },
  {
   "id": "altitude",
   "name": "Altitude range / high / low",
   "level": 1,
   "cheat": 1,
   "wrong": 3,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "alt_range has NO hysteresis/noise filter (unlike _ascent_m which uses 3m): a single GPS/baro altitude spike directly sets max-min, so the swing is dominated by sensor error, not real elevation change",
    "Boards are explicitly ungated ('a trivial ride can't move their max/min') -- but that assumption is wrong: one bad sample CAN move max/min/range with zero real riding, so a stationary wheel or 2-second log can top the board",
    "No GPS/horizontal corroboration: altitude is never cross-checked against haversine distance, so a parked wheel with drifting baro/GPS altitude inflates the swing and a wandering absolute max",
    "Parser only rejects NaN/inf -- no plausibility bounds, so absurd readings (e.g. -30000m, 9000m, baro reset to 0/sea-level) pass straight into min/max; min_altitude_m is a MIN board, so a single garbage negative reading instantly wins 'Lowest Altitude'",
    "Barometric altitude is uncalibrated and weather/pressure dependent and can read hundreds of meters off or negative; mixing baro-sourced and GPS-sourced trips makes cross-rider comparison meaningless",
    "Measures the device's reported altitude extremes, not rider-achieved elevation: a gondola/car/plane/elevator ride or a log spanning an airport would crush the board"
   ],
   "fixes": [
    "Gate these boards by qualifying ride length and GPS distance like the spikeable metrics; apply the same hysteresis used by ascent and clamp altitudes to plausible bounds and reject outliers vs neighbors; require horizontal-movement corroboration and ideally flag/separate barometric vs GPS altitude before comparing riders.",
    "Apply hysteresis/outlier rejection like _ascent_m (drop per-sample jumps beyond a plausible vertical-speed cap, e.g. reject |delta| implying > ~5-10 m/s), require GPS corroboration and a moving/distance gate, and put alt_range/max/min through gated_leaderboard with min_s and min_km so spiky short logs can't win.",
    "Route alt_range through the gated path (min_s/min_km + wheel blocklist) with GPS corroboration, apply hysteresis and absolute plausibility clamps (e.g. -500..6000m, reject jumps > a few m/s), and require lat/lon present for altitude to count; ideally derive from barometric data when available."
   ],
   "hows": [
    "Per-trip alt_range_m is the raw max(alt)-min(alt) of GPS/baro altitude samples, while max_altitude_m/min_altitude_m are the raw per-trip max and min; these feed the ungated Altitude King (swing), Max Altitude and Min Altitude boards with no qualifying ride length, distance, or noise filtering.",
    "Per trip it takes the raw max and min of every altitude sample: alt_range_m = max(alt) - min(alt), with max_altitude_m/min_altitude_m as the bare extremes. The visible 'altitude_king' board ranks riders by their best per-trip alt_range with no duration/distance gate, only > 0.",
    "alt_range_m = max(alt) - min(alt) over a ride's raw altitude samples (GPS/baro altitude column straight from the CSV, only NaN/inf stripped); max_altitude_m/min_altitude_m are the raw per-trip extremes. The altitude_king board ranks riders by their best per-rider alt_range with no gate beyond positive_only."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "battery_range",
   "name": "Battery drain / range / efficiency",
   "level": 1,
   "cheat": 1,
   "wrong": 3,
   "cheat_max": 2,
   "wrong_max": 3,
   "issues": [
    "est_range_km is a naive linear extrapolation that assumes battery % is linear with distance; with only a 10% drain floor and NO minimum-distance gate, a short downhill/coasting ride that nips just past 10% yields an absurd range (5km/10% = 50km).",
    "best_range_km / best_wh_per_km are per-rider single-trip bests in RiderStat with no qualifying-trip gate (efficiency/range leaderboards only filter >0), so one freak coast/regen trip sets the record.",
    "Battery % comes straight from the wheel BMS with no corroboration; BMS readings are coarse and jumpy (post-rest % 'recovery', voltage-sag dips), so sparse sampling undercounts drain (inflating range, lowering Wh/km) while spikes overcount it.",
    "wh_per_km uses regen/braking power that is summed into the integral; a downhill or regen-heavy run drives total Wh down, gaming the 'most efficient' (min Wh/km) board.",
    "battery_used_pct (Battery Drain / battery_vampire) is directly gameable: stationary wheel-on draw, lights/electronics, or wheel-spin all drop battery % without real riding, and the board rewards biggest drain.",
    "range and efficiency are both derived from `distance`, which can be GPS- or odometer-based and is independently spoofable; inflated distance with real drain inflates range and improves efficiency simultaneously."
   ],
   "fixes": [
    "Gate est_range_km and wh_per_km on a meaningful minimum distance (e.g. >=5-10 km moving) AND larger battery drop (e.g. >=20-25%), clamp/round est_range to sane bounds, clamp negative (regen) power to 0 in the Wh integral, and apply the same qualifying-trip gate (min_s/min_km, validated) used by other spikeable boards to the range/efficiency leaderboards instead of trusting a single per-rider best.",
    "Cap est_range_km to a sane max (e.g. <=200 km) and require a meaningful battery drop AND GPS-corroborated distance with a min-km gate; derive battery consumption from net SoC change (or a sustained/smoothed window) rather than summing transient sag dips; gate range/efficiency boards by duration+distance and add plausibility caps on wh_per_km and est_range_km.",
    "Require a larger battery drop (e.g. >=25%) and impose plausibility caps (reject est_range_km and Wh/km outside per-wheel-class bounds); compute drain as net first-minus-last SoC (or median-filtered) to kill noise wiggles, exclude trips with significant regen/charging from range/efficiency, and require GPS-corroborated distance plus integrated-energy/SoC agreement before a trip qualifies."
   ],
   "hows": [
    "battery_used_pct sums all BMS battery-% drops over a ride (ignoring recovery/charging); est_range_km linearly extrapolates distance*100/drop once drop>=10%; wh_per_km integrates power (or V*I) over time and divides by trip distance. None of these corroborate the BMS %, power channel, or distance against an independent source.",
    "battery_used_pct sums every downward step in the (voltage-derived) battery % (ignoring charging); est_range_km linearly extrapolates distance*100/battery_used_pct (only when >=10% used); wh_per_km integrates power over distance; min_battery_pct is the single lowest battery sample. Aggregation keeps the BEST per rider: max est_range_km, min wh_per_km, max battery_used_pct, min min_battery_pct.",
    "battery_used_pct sums every downward step of the BMS battery-% reading (charging/regen ignored); est_range_km linearly extrapolates dist*100/battery_used_pct gated at a 10% drop; wh_per_km integrates reported power over time divided by trip distance, and the boards rank max drain, max range, and min Wh/km."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "ascent",
   "name": "Climbing / biggest single climb",
   "level": 1,
   "cheat": 1,
   "wrong": 3,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "Raw GPS altitude is noisy (+/-10-30 m); a 3 m hysteresis is far below that noise floor, so one-directional drift over a long stationary or sparse log silently accumulates as fake gain",
    "Zero corroboration: ascent uses s.alt alone (no GPS lat/lon movement, no haversine, no _corrob_speed equivalent), so a parked wheel logging drifting altitude for hours racks up climb with no actual riding",
    "plausibility.check has NO altitude/ascent rule at all (no impossible-ascent cap, no cross-check vs horizontal distance) while distance gets unverified_distance/teleport guards -- ascent is unguarded",
    "The visible 'ascent' (lifetime sum) and 'peak' boards are not behind the duration/distance gate, so a tiny noisy trip qualifies; lifetime sum also rewards volume of noise across many trips",
    "Name 'biggest single climb' is misleading: ascent_m is cumulative gain over a whole ride (every roller and noise spike added), not one continuous climb, so a flat ride with many small ups can beat a real mountain",
    "No unit sanity check: a feet-vs-meters altitude source or a barometric-vs-GPS mix would inflate ~3.3x with nothing to catch it"
   ],
   "fixes": [
    "Gate both boards by min distance/duration, corroborate ascent against horizontal GPS movement (require lat/lon travel and reject ascent on near-stationary logs), raise hysteresis to ~5-10 m to exceed GPS-altitude noise, add a plausibility cap on m/km grade, and rename 'peak' to 'total elevation gain' rather than 'single climb'.",
    "Gate ascent on corroborated horizontal movement (only accumulate when moving >2 km/h between samples), cap per-sample altitude delta to a believable climb rate (e.g. reject >max_climb_m_per_s), and smooth/median-filter altitude before applying the hysteresis to break the noise-rectification ratchet.",
    "Rename/clarify the summed board as 'total elevation gain'; add a GPS-corroboration gate (require horizontal movement and a plausible grade per step), cap per-sample climb rate against ground speed, and apply a min-distance/min-duration qualifying gate like the other anti-gaming boards."
   ],
   "hows": [
    "_ascent_m sums positive elevation deltas from the per-sample altitude field (raw GPS/baro 'altitude' column) using a 3 m hysteresis to swallow small wiggles; the 'ascent' board ranks the lifetime SUM (total_ascent_m) and 'peak'/peak_bagger ranks the largest single-trip ascent_m.",
    "_ascent_m sums altitude (s.alt) increases over a trip, only counting an up-move once it exceeds a 3m hysteresis above a running reference (down-moves only reset the ref, never subtract); the 'ascent' board sums this across all trips while 'peak' (peak_bagger) takes the single biggest per-trip ascent_m.",
    "_ascent_m sums altitude increases per trip using a 3m hysteresis to filter wiggles; the public 'ascent' board ranks riders by total_ascent_m, a CUMULATIVE LIFETIME SUM of every trip's ascent_m (the separate 'peak' board is the true biggest single climb)."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h). Plus an impossible-ascent plausibility rule (>300 m/km)."
  },
  {
   "id": "streak",
   "name": "Day streak",
   "level": 3,
   "cheat": 3,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "No minimum distance gate: add_daily is called with dist = trip.distance_km or 0.0, so even a 0 km or few-meter validated trip creates a day row, making the streak trivially farmed with one tiny upload per day (stationary wheel-spin / parking-lot blip / coast counts).",
    "current_streak is never decayed against today: it is only recomputed when a NEW trip is applied, and _recompute_streak measures the run ending at dates[-1] (last ride), not now, so a rider who stopped riding months ago still displays a large 'current' streak indefinitely.",
    "Day boundary uses trip.start_utc.date() (UTC) and ignores the Trip.tz / tz_known fields, so riders in non-UTC zones get evening rides misattributed across midnight, inflating or breaking streaks by timezone rather than behavior.",
    "Streak depends only on trip existence with zero GPS/sensor corroboration of real movement, distance, or speed integrity.",
    "A single ride that spans UTC midnight does not create two day-rows (start_utc only), so long late-night rides get no credit while two short rides minutes apart across midnight do — inconsistent.",
    "longest_streak (the ranked value) is permanent and can never be lost, so the board rewards a one-time past habit, not current consistency."
   ],
   "fixes": [
    "Require a real minimum (e.g. >=1 km AND non-trivial moving_s) before a day counts toward a streak, derive the day from the rider's local timezone (tz/tz_known) not raw UTC, and recompute current_streak relative to today (break it if last_ride_date < yesterday) via a daily job rather than only on new uploads.",
    "Only create/count a daily row when the day's qualifying riding clears a real threshold (e.g. >=1-2 km AND some moving_s), key days by the rider's local date instead of UTC, and reject backfilled trips with start_utc far in the past (or recompute streak only over non-backdated days) to stop retroactive streak repair.",
    "Require a minimum real ride to count a streak day (e.g. moving_s >= N and distance_km >= ~0.5 km, mirroring the gated boards), bucket the date by the rider's local timezone instead of UTC, and anchor current_streak to today/yesterday (reset to 0 if the latest ride date is older than 1 day) so it reflects an active streak."
   ],
   "hows": [
    "Counts consecutive UTC-calendar days that have any validated trip (a DailyDistance row): longest_streak is the best-ever run and current_streak is the trailing run ending at the rider's LAST ride date; the public board ranks by longest_streak.",
    "Counts the longest (and current) run of consecutive UTC calendar dates on which the rider has any DailyDistance row, recomputed from sorted daily dates via _recompute_streak; a daily row is created by add_daily for every validated trip with a start_utc, with no minimum distance or duration.",
    "For each validated trip with a start time, a DailyDistance row is upserted keyed on the UTC calendar date (start_utc.date()) with no minimum distance; _recompute_streak then counts the longest run of consecutive calendar days that have any row (longest_streak, the ranked value) and the trailing run back from the most recent date (current_streak)."
   ],
   "reviews": 3,
   "mitigated": None
  },
  {
   "id": "distance",
   "name": "Distance (total / day / week / month)",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 2,
   "issues": [
    "Stationary wheel-spin inflates it for free: odometer climbs while GPS reads ~0, summarize takes odo because gps_km<=0 (summary.py L465), and NO flag fires (unverified_distance needs ZERO GPS samples; distance_mismatch needs gps>0.5km). Spin on a stand = free km.",
    "Odometer is self-reported telemetry (the literal 'total mileage' column) with no GPS corroboration when chosen, so any client that fabricates or edits the log fabricates distance directly.",
    "40% slack both ways: odo is preferred even when ~40% above GPS, and distance_mismatch only flags >40% disagreement, so steady 0-40% over-reading passes silently and compounds across many trips.",
    "odo_max_step_km=5.0 is a fixed absolute with NO implied-speed bound (docstring admits this); a glitchy/malicious log injecting many sub-5km positive jumps accumulates unchecked.",
    "GPS-only fallback sums every tiny haversine hop with no accuracy/HDOP gate and no stationary threshold, so parked jitter and walking become distance; mock_location is only a flag, teleport needs >8 jumps to fire.",
    "Flagged/unverified high-distance trips are not deleted and can be admin-approved later, bypassing the automatic guard; daily/week/month boards sum DailyDistance with no further plausibility, so one inflated trip permanently skews best-day/week/month records."
   ],
   "fixes": [
    "Require GPS corroboration before crediting odometer distance: when GPS is near-zero but odo is large, flag instead of silently accepting; cap odo at gps*(1+tol) when GPS exists, add an implied-speed bound and tighten odo_max_step_km, lower the mismatch tolerance, and keep unverified/mismatched distance off the boards rather than admin-approvable by default.",
    "Bound odometer distance by GPS when GPS exists (reject/clamp odo that exceeds gps_km beyond tolerance, not only when it falls below), require corroborated movement before crediting an odo step, make the step cap speed/dt-aware, and add a per-trip distance plausibility gate (distance vs duration/realistic speed) before it feeds total_km and DailyDistance.",
    "Cap or reject per-sample odometer growth that has no corroborating GPS/wheel-speed movement (require min(odo_delta, gps_or_speed_implied_movement) when GPS exists), tighten odo_max_step_km to a believable per-sample distance, and apply a moving_s/min-speed sanity gate to the mileage/daily/week/month boards so pure freespin can't accrue distance."
   ],
   "hows": [
    "Per-trip distance prefers the wheel's lifetime odometer (sum of positive deltas, rejecting resets and any single step >5km) over GPS haversine, defaulting to odo whenever odo>0.1km and odo>=gps*0.6 (or gps<=0); trip distances sum into RiderStat.total_km and bucket into DailyDistance, which feed the mileage/daily/week/month boards.",
    "Per-trip distance_km is the wheel odometer delta-sum (positive steps <=5 km, resets/negative deltas rejected) preferred over GPS haversine distance, falling back to GPS only when there is no odometer or the odo reads far below GPS; mileage sums per-rider total_km and daily/week/month aggregate DailyDistance rows with no further per-trip corroboration.",
    "Per-trip distance_km is taken from the wheel's self-reported odometer (summing positive deltas <=5 km, no GPS check) and only falls back to GPS haversine distance when the odometer is missing/too low; total_km, daily, week and month boards sum these per-trip values with no qualifying gate."
   ],
   "reviews": 3,
   "mitigated": "GPS-corroborated — a stationary wheel-spin no longer credits km."
  },
  {
   "id": "stop_time",
   "name": "Emergency stop times",
   "level": 1,
   "cheat": 1,
   "wrong": 3,
   "cheat_max": 2,
   "wrong_max": 3,
   "issues": [
    "No minimum-dt floor: unlike _fastest_0_40 (min_s=1.5) only dt>0 is required, so a single sample reading 30+ followed by a sample reading <=2 produces a sub-second 'stop'.",
    "No deceleration plausibility cap: _speeds clamps acceleration but nothing here rejects physically impossible braking (e.g. 50->0 in 0.4s ~= 35g), so spikes/glitches win the board.",
    "Any speed dropout reads as a stop: corrob_speed=min(wheel,gps) means the instant EITHER signal drops (BT/log dropout, power-off, wheel lifted, GPS noise at low speed) it satisfies <=2 km/h, faking a hard stop.",
    "No ramp/intermediate-sample check: it never verifies a believable monotonic decel between the fast sample and the stop sample, so a teleport from fast to zero counts.",
    "Sparse/quantized sampling (e.g. 1 Hz) makes timing luck-based and floors achievable resolution; whoever has a sample landing right at standstill wins.",
    "Trip-level gate (min_s/min_km on whole ride) does not validate the individual stop event, so one glitch in a long legit ride still registers."
   ],
   "fixes": [
    "Add a minimum-dt floor and a max-deceleration cap (reject implied braking above a believable g, e.g. >1.5g), require >=2-3 intermediate samples showing a monotonic decel ramp through the band before counting, and gate the stop event itself (entry speed corroborated by GPS, not just the trip).",
    "Add a min-dt floor and an implied-deceleration cap (reject dt implying > ~1g or below a noise floor), linearly interpolate both the from_kmh and the 2 km/h crossings like _fastest_0_40, and require the stop to be corroborated by both wheel and GPS (or wheel-only) rather than firing on the lower of the two.",
    "Add a min_s floor and a max physical-deceleration cap (reject dt implying >~1g braking), require a sustained dwell at the entry speed and 2+ consecutive samples below the standstill threshold, require GPS corroboration (or a max inter-sample gap) for both endpoints, and reject trips whose stop spans a sampling gap; consider using a robust percentile across trips instead of the raw min."
   ],
   "hows": [
    "_fastest_stop scans samples for the shortest elapsed time between the last sample at/above from_kmh (30 or 50) and the next sample whose corroborated speed (min of wheel and GPS) falls to <=2 km/h, reporting the minimum such dt per trip; lower is better and re-accelerating above from_kmh resets the start.",
    "_fastest_stop walks samples and records the shortest positive time between the latest sample at/above from_kmh (30 or 50, corroborated as min(wheel,gps)) and the next sample at/below 2 km/h; lower wins on a gated 'min' board. It does not interpolate the crossings and imposes no minimum-duration or implied-deceleration floor.",
    "stop_30_s / stop_50_s = _fastest_stop: the shortest time between the last corroborated-speed sample at/above 30 (or 50) km/h and the next sample at/below 2 km/h, with re-acceleration above the entry speed resetting the window; the gated board takes the MIN across a rider's trips (lower is better)."
   ],
   "reviews": 3,
   "mitigated": "Speed-corroborated / interpolated — can't be faked while stationary."
  },
  {
   "id": "freespin",
   "name": "Freespin",
   "level": 3,
   "cheat": 3,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "raw_max=max(wheel_speeds) uses s.speed only; GPS/_corrob_speed is NOT applied to the reported value, so a lifted, hand-spun wheel reading 150+ km/h with GPS~0 sets the score",
    "The board literally rewards the fakeable behavior (off-ground motor spin / throttle twist) — trivially inflatable to any value with zero physical risk or real riding",
    "Ungated: freespin_leaderboard reads RiderStat.best_freespin directly (not gated_leaderboard); aggregator updates it with no min_s/min_km check, so a 1-sample, 0-km trip can set the record",
    "Unbounded and never flagged: plausibility checks impossible_speed on max_speed but not on max_freespin, so a single corrupt/parse-error sample (e.g. 65535 raw, decimal/unit slip) becomes a permanent leaderboard record",
    "Conflates genuine off-ground freespins, sensor glitches, dropouts, and unit/parse errors into one celebrated number",
    "Sparse sampling: one dropout or glitch sample between readings qualifies as freespin even on an otherwise clean ramp"
   ],
   "fixes": [
    "Corroborate the spike against GPS (require GPS~0 to confirm a true off-ground freespin and reject as a glitch otherwise), clamp to a sane ceiling, flag/exclude single-sample spikes, and route the board through gated_leaderboard with a min duration/distance + per-wheel plausibility cap.",
    "Treat freespin as a non-ranked warning flag only, or if ranked, cap/clamp the reported value and require GPS corroboration of near-zero ground speed plus a wheel-RPM sanity ceiling; at minimum route it through gated_leaderboard with a min-distance gate so stationary stunts cannot qualify.",
    "Treat freespin as a non-competitive warning/badge rather than a ranked leaderboard; if ranked, gate it (min distance/duration of a real ride), cap the recorded value to a sane ceiling, require GPS-zero corroboration that the wheel was actually free-spinning, and drop single-sample spikes."
   ],
   "hows": [
    "Reports the raw peak wheel-reported speed (max of s.speed, with NO GPS corroboration) but only when it beats the acceleration-limited 'realistic' speed by freespin_margin (5 km/h) — i.e. an un-ramped instantaneous spike. Rolled up per rider as best_freespin via a plain max with no duration/distance gate.",
    "Reports the raw max wheel speed of a trip (max of wheel_speeds, NOT GPS-corroborated) but only when that peak exceeds the acceleration-limited 'realistic' speed by freespin_margin (5 km/h); the leaderboard ranks riders by the single biggest such spike (best_freespin = max across trips).",
    "It reports the single highest raw wheel-speed sample (max(wheel_speeds)) of a trip, but only when that raw peak exceeds the acceleration-limited 'realistic' speed by freespin_margin (5 km/h); the leaderboard takes the lifetime max of that value per rider. It measures the size of a rejected speed spike (wheel lifted/free-spinning, sensor glitch, or crash), not real riding."
   ],
   "reviews": 3,
   "mitigated": None
  },
  {
   "id": "gforce",
   "name": "G-force (2s / 4s / 6s)",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "Raw, uncorroborated IMU value parsed by column name with zero validation beyond a >12g reject (12g over 2s is already absurd for an EUC, so the gate catches nothing real)",
    "IMU is almost certainly the phone's, so it measures phone handling/road buzz/pocket jostle, not wheel cornering or braking",
    "Trivially gamed while stationary or off-wheel: shake/tap the phone, rev or bounce the wheel, ride a washboard road, or drop a curb for 2s of sustained high abs(g) with no real riding",
    "No gravity-baseline subtraction or orientation normalization: 'g-force' may or may not include the static 1g, so values are inconsistent across phones/mounts/app versions and not comparable rider-to-rider",
    "Unlike speed it has no corroboration (no min-of-wheel/GPS like _corrob_speed); the dedicated speed-derived accel_g/brake_g and g_fast_* boards exist precisely because the codebase itself treats IMU g as fakeable ('the fakeable IMU brake/lateral boards were dropped')",
    "Base gforce_leaderboard uses best_gforce with only positive_only (no time/distance gate); g4/g6 gates only require a long-enough ride somewhere in the log, not that the spike happened while genuinely moving"
   ],
   "fixes": [
    "Demote/retire the raw-IMU g boards in favor of the speed-derived longitudinal g (accel_g/brake_g) and speed-gated g_fast_* boards; at minimum require a per-sample speed gate (abs(g) only counted while corroborated speed > a threshold), subtract the 1g gravity baseline, and tighten the plausibility cap far below 12g.",
    "Gate the public g-force board on corroborated moving speed (like g_fast_*), lower the plausibility ceiling to a realistic EUC bound (~3-4 g) and clamp rather than only flag, and require a minimum sample density within the window so sparse logs cannot inflate the average.",
    "Apply the same qualifying gate (min_s/min_km) to the main board, require corroborated speed above a threshold during the g window (reuse _g_fast logic) so stationary shaking is excluded, time-weight _sustained_max instead of sample-count averaging, and clamp/flag physically implausible |g| (>~1.5g)."
   ],
   "hows": [
    "Takes the raw 'G-force' IMU column from the app CSV verbatim (no clamping/calibration/GPS corroboration) and reports the highest 2s rolling average of abs(g) as the leaderboard value; g_sust_4s/6s are the same over 4s/6s windows feeding gated 'G-Hold' boards.",
    "Takes abs() of the app-reported 'g-force' IMU column and reports the highest average over any trailing ~2s window (4s/6s variants widen the window); the public board ranks RiderStat.best_gforce with no speed/GPS corroboration.",
    "Takes the raw scalar 'g-force' column the logging app writes per sample, then reports the highest 2s (or 4s/6s for the hidden G-Hold boards) trailing-window average of its absolute value via _sustained_max(abs(g), window); the public board is max(max_gforce) across all of a rider's trips."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "pwm",
   "name": "PWM peak / 3s",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "Raw, uncorroborated passthrough (parser CANON 'pwm' -> s.pwm); unlike speed/g-fast metrics there is NO speed or GPS gate, so a stationary stunt counts.",
    "PWM duty peaks at LOW speed under load (stall, pushing against a curb, lifting and stalling the wheel) — the exact parking-lot trick the speed-derived boards were built to defeat; you do not need to ride fast at all.",
    "Only the per-trip duration/distance gate (GATE_TIERS) defends it; within any qualifying ride a single 3s stall wins, and the 3s average barely raises the bar.",
    "No cross-firmware normalization: PWM is reported as 0-1 by some apps and 0-100 by others (and can clamp at/over 100%); max() across all wheels with no scaling makes the board firmware-dependent, not skill-dependent.",
    "Sensor glitches/spikes and braking/regen can momentarily read high duty, so the '% closest to maxing the motor' framing can be misleading.",
    "WHEEL_FIELD_METRIC maps pwm to the coarse 'speed' data-quality group, so per-model blocking is blunt and may not catch a mis-scaled PWM channel."
   ],
   "fixes": [
    "Gate PWM windows to require corroborated speed above a threshold (e.g. reuse _g_fast-style speed gating) and/or only count PWM while accelerating, plus per-firmware PWM scale normalization (detect 0-1 vs 0-100) before ranking.",
    "Gate PWM on corroborated speed (only count duty while _corrob_speed >= a real threshold, like the g_fast metrics), drop the raw single-sample max_pwm board in favor of the sustained value, clamp/normalize to 0-100 per wheel model, and reject samples where GPS speed is ~0 to kill freespin inflation.",
    "Gate PWM on corroborated speed: only count samples where _corrob_speed(s) is above a real threshold (e.g. >=15-20 km/h with a live GPS fix), normalize/clamp PWM to a single 0-100 scale per firmware, and reject negative (braking) values before averaging."
   ],
   "hows": [
    "Takes the raw firmware-reported PWM/motor-duty-cycle column straight from the CSV and reports its peak (max_pwm) and best 3-second rolling average (pwm_sust_3s via _sustained_max), with no corroboration against speed, GPS, or any other channel.",
    "Reports the motor's PWM duty cycle (%) straight from the app's CSV: max_pwm is the single-sample peak and pwm_sust_3s is the best 3-second rolling average, with no clamping, no plausibility check, and no corroboration against speed, GPS, or load.",
    "Takes the wheel firmware's raw PWM (motor duty-cycle / saturation %) field and reports its highest 3-second rolling average (pwm_sust_3s) plus the instantaneous peak (max_pwm), fed to a gated max board that only checks trip duration and distance."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "voltage",
   "name": "Peak voltage & sag",
   "level": 3,
   "cheat": 2,
   "wrong": 3,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "Peak voltage is essentially nominal-pack-voltage + state-of-charge: a 151V wheel fresh off the charger beats an 84V wheel no matter the riding; it ranks hardware/charge, not rider skill",
    "Peak voltage board is UNGATED (no min_s/min_km/movement/GPS check) so a stationary, just-powered-on wheel at full charge sets the record; no corroboration at all",
    "Peak voltage = max() of a single sample, so one positive sensor spike/glitch wins; no spike vs sustained distinction unlike g-force/power metrics",
    "Voltage sag rewards the WORST-behaving setup: a stronger pack with less sag loses; it perversely celebrates weak packs, loose connectors, BMS hiccups",
    "Sag's 'base' is the max within the same rolling window, so one high spike inflates every later sag reading; sparse sampling can put a high baseline and a low glitch in one window and fabricate a large drop with zero load",
    "Neither value verifies the wheel was under actual load/movement when the dip occurred, so sag is not necessarily 'under load' as the name claims"
   ],
   "fixes": [
    "Gate peak voltage like the others (min duration/distance + movement) or drop it from public boards since it mostly reflects pack chemistry; for sag, require the dip to coincide with high current/power and use a sustained (multi-sample) dip rather than a single-sample min, and normalize by nominal pack voltage so it measures % sag under real load.",
    "Drop or hide peak_voltage as a rider feat (it measures hardware, not skill); if kept, normalize per wheel/series count and gate it. For sag, require concurrent high current (corroborate it is load-induced) and reject single-sample dips/glitches via a median-filtered baseline.",
    "Drop peak_voltage as a leaderboard (it's a spec sheet, not an achievement) or normalize it per nominal pack voltage; for sag, require concurrent high current/power, reject single-sample voltage spikes, and rank percentage sag rather than absolute volts."
   ],
   "hows": [
    "Peak voltage is max(sample voltage) over a trip, kept as the rider's all-time max and shown on an UNGATED public board (positive_only only); voltage sag is the biggest dip below a rolling 5s peak (_max_voltage_sag), shown on a duration/distance-gated board.",
    "peak_voltage is the plain max() of all raw voltage samples in a trip; the separate sag board uses _max_voltage_sag, the biggest dip below the rolling 5s peak. Peak-voltage uses no gate, no GPS/current corroboration, and no plausibility clamp.",
    "peak_voltage is the single highest raw voltage sample of the trip (no spike rejection, no gate); max_voltage_sag is the largest drop of voltage below its own peak within a rolling 5s window. Boards rank peak_voltage (ungated) and sag (gated by min duration/km)."
   ],
   "reviews": 3,
   "mitigated": None
  },
  {
   "id": "power",
   "name": "Power 2s / 6s",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 2,
   "issues": [
    "No speed corroboration at all (unlike speed/g/accel boards): lifting the wheel and gunning the throttle, or pushing into a wall/curb, draws huge sustained amps and tops the board while stationary",
    "Gate only checks whole-trip duration/distance (>=300s/1.5km), not the power window itself, so you stunt for the 2s/6s window then ride normally to qualify",
    "_sustained_max is a plain mean of in-window SAMPLES, not time-weighted; sparse/dropout sampling (e.g. 2 readings 2s apart) lets one spike pass as 'sustained' with no min-sample or coverage check",
    "_power has no plausibility cap: a single voltage or current sensor glitch multiplies into wattage, and reported s.power is trusted as-is",
    "No unit normalization or abs/regen handling; mixed-firmware W scaling or negative/regen current is taken raw",
    "Inherently rewards high battery voltage (134V vs 84V wheel makes far more watts at same throttle), so it ranks hardware not rider effort"
   ],
   "fixes": [
    "Require the power window to overlap corroborated speed/PWM above a threshold (reject stationary spin), make _sustained_max time-weighted with a min sample-coverage gate, and clamp _power to a plausible per-wheel ceiling; consider normalizing by battery voltage or moving to current-only.",
    "Cap per-sample power to a model-plausible ceiling, require window samples to span a real fraction of window_s (e.g. >=60%) and average by time not count, and gate power windows on corroborated speed > a few km/h to exclude stationary freespin.",
    "Gate power samples on _corrob_speed >= a real moving threshold (e.g. >5-10 km/h) so stationary spins/load-tests don't count, time-weight the window average instead of dividing by sample count, and clamp _power to a per-wheel plausible max (and ignore volts*amps when V and A spikes aren't time-aligned)."
   ],
   "hows": [
    "max_sustained_w/power_sust_6s is the highest trailing 2s/6s mean of _power (reported watts, else voltage*amps) over a trip, then the max across a rider's trips, shown on a duration/distance-gated board. It is a purely electrical measure with no speed/GPS corroboration.",
    "Takes the best trailing ~2s (and 6s) average of per-sample power, where power is the reported power channel or else voltage*current, via _sustained_max which averages by sample count over the window. No GPS/motion corroboration and no plausibility cap.",
    "Takes the max trailing-window average (2s or 6s) of per-sample power, where power is the reported s.power or, failing that, the product volts*amps. The window only bounds the time span; the average divides by sample count, with no speed/GPS corroboration and no plausibility ceiling."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "sustained_accel",
   "name": "Sustained acceleration (Rocket)",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 2,
   "issues": [
    "Uses raw s.speed, NOT _corrob_speed — it is the ONLY speed-derived metric in the file that skips GPS corroboration; _corrob_speed exists specifically to reject freespin where the wheel reads fast but GPS reads ~0",
    "Stationary wheel-spin / freespin trivially games it: lift the wheel, ramp it 0->high over 2-6s, and that fake ramp becomes a huge sustained_accel with zero GPS check",
    "The realistic-speed acceleration clamp (max_accel=20 km/h/s in _speeds) is never applied here, so values are not sanity-checked against believable physics; a freespin already excluded from the Speed board still pollutes Rocket",
    "No upper cap on the rate: two glitchy/noisy readings ~2s apart pass the lo-window guard and a single sensor jump can set the per-trip max",
    "Per-trip max with no corroboration means one bad sample window defines the record; sparse sampling (~2-3s spacing) makes the very first pair satisfy dt>=lo, so noise dominates",
    "Gate (min duration + min km tiers) requires a real ride but does NOT prevent a single wheel-spin or spike moment inside an otherwise normal commute"
   ],
   "fixes": [
    "Compute it from _corrob_speed (min of wheel and GPS) like every other launch/brake metric, and additionally clamp the per-window rate to max_accel (or null it when the window's speed exceeds the realistic/freespin-rejected track) so a freespin can't set the record.",
    "Switch pts to _corrob_speed (drop samples where GPS contradicts wheel), require the speed rise to be near-monotonic across the window (or take the min per-step slope / true windowed average rather than endpoint slope), add a sanity cap (reuse max_accel) and a noise floor, and reuse the freespin flag to exclude spin-up episodes.",
    "Compute sustained_accel from _corrob_speed (min of wheel and GPS) and clamp the slope at the admin max_accel ceiling, mirroring _speed_g; reject windows whose endpoint speed was diverted to max_freespin."
   ],
   "hows": [
    "Brute-force scans all sample pairs and reports the highest km/h-per-second speed increase held for at least the lo window (2s) and at most hi (6s), taken as a per-trip max and surfaced on the gated 'Rocket' board. Lower-bound window is meant to reject one-sample spikes.",
    "_max_sustained_accel scans raw wheel speed (s.speed, NOT GPS-corroborated) and reports the steepest two-endpoint slope (km/h per s) between any pair of samples spaced lo..hi seconds apart (2-6s); it is the leaderboard 'Rocket' value, gated by per-trip duration/distance tiers.",
    "Scans every pair of samples and takes the max average wheel-speed slope (km/h per s) over any window between lo (2s) and hi (6s), reporting the strongest one; the live board is gated by ride length/distance."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "temperature",
   "name": "Temperature high / low",
   "level": 1,
   "cheat": 1,
   "wrong": 3,
   "cheat_max": 2,
   "wrong_max": 3,
   "issues": [
    "Single-sample extreme: one glitchy reading (disconnect spike like 200 deg, a 0/-40 error code, sensor dropout) wins the board; unlike g-force there is no sustained-window or spike filter",
    "Low Temp is trivially gamed: power the wheel on in a cold garage/freezer and log a near-stationary qualifying trip; the gate never requires the cold value come from riding",
    "High Temp is largely environmental (hot climate, parked in sun, board in a hot bag) rather than rider effort, and perversely rewards a near-thermal-runaway / unsafe condition",
    "Unit and source ambiguity: temp may be board/MOSFET vs battery sensor and degC vs degF across firmwares, making cross-rider comparison meaningless",
    "No corroboration against current/power/speed: a faulty sensor reading hot while idle is accepted as a record",
    "Duration/distance gate does almost nothing for min_temp since the extreme can occur at a standstill"
   ],
   "fixes": [
    "Use a short sustained window (e.g. require temp held N seconds) instead of a single-sample max/min, clamp to a sane physical range (e.g. -30..120 C) to drop spikes/error codes, and require concurrent movement/load (corroborated speed or current > 0) for the extreme to count; normalize units and document the sensor source.",
    "Use a robust sustained value (e.g. best N-second rolling average or a high/low percentile) instead of raw min/max, clamp to a physical plausibility band (reject e.g. <-40 or >150C and known sentinels), and detect/normalize C vs F per source before ranking.",
    "Normalize/clamp temp at ingest to a sane Celsius range and drop out-of-range as None; use a short sustained window (e.g. median/2s avg) instead of the raw single-sample extreme; segregate by reported sensor channel/wheel model (or auto-block models whose temp channel is ambiguous) so brands are not compared apples-to-oranges."
   ],
   "hows": [
    "Per-trip max_temp/min_temp are the plain max/min of every sample's temp reading, with no spike rejection, range clamp, or corroboration, so a single sample sets the record. Boards are gated only on trip duration + distance, not on temperature being reached by riding.",
    "max_temp/min_temp are the raw max()/min() of the single 'temperature' CSV column over a trip's samples, surfaced as gated 'High Temp'/'Low Temp' boards. No unit normalization, no plausibility clamping, no per-source definition of what 'temperature' means, and no physics/GPS corroboration.",
    "max_temp/min_temp are the single highest and lowest values from the CSV 'temperature' column over a ride, exposed as gated max/min boards (min duration + min km, validated trips only). No averaging, no spike rejection, no unit normalization."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "time_counts",
   "name": "Time-of-day & day-type counts",
   "level": 3,
   "cheat": 2,
   "wrong": 3,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "start_utc is true UTC (parser subtracts tz_offset_min, or uses the app's UTC field), but the filters use UTC hours/day-of-week directly, so 'night'/'early'/'weekend'/'commuter' are measured in UTC, not local time despite being inherently local concepts",
    "Longitude bias: a UTC+8 rider's 06:00 local ride is stored 22:00 UTC -> wrongly counted as night_rider, not early_bird; a UTC-8 rider's 22:00 local ride lands at 06:00 UTC -> wrongly counted as early_bird",
    "Weekend/big_day/commuter day-of-week is UTC: a Saturday-evening or late-Sunday local ride in a non-zero offset zone rolls into a different UTC day, misclassifying weekend vs weekday and splitting/merging a 'big day'",
    "tz / tz_offset_min are captured and stored on the Trip but never used by these boards, so the fix data already exists and is simply ignored",
    "Count boards (night, early, frequent, bigday) have no min_km/min_s gate, unlike gated_leaderboard, so a flurry of trivial sub-100m or parked stop-start 'trips' inflates counts; weekend/commuter inherit any distance-inflation (freespin/odometer) since they sum distance_km",
    "Riders can trivially game by choosing/spoofing the upload's tz_offset_min (clamped only to +-14h) or by chopping one ride into many short trips to pad night/early/bigday counts"
   ],
   "fixes": [
    "Bucket on local wall-clock time: add a tz_offset_min column (already received) and filter on strftime over (start_utc + offset), or store a precomputed local_hour/local_dow per trip; additionally apply a per-trip min_km/min_duration gate so padding with micro-trips can't inflate the counts.",
    "Convert start_utc to the rider's local time before bucketing (use stored tz/tz_offset_min via zoneinfo as services/telegram.py already does), exclude tz_known=false trips, and add a per-trip distance+duration gate (and ideally count distinct ride sessions, not raw trip rows) for the count-based boards.",
    "Convert start_utc to the rider's local time (store a per-trip tz offset from start_lat/start_lon or device tz) before strftime hour/day bucketing, and apply a minimum distance+duration qualifying gate to the count boards so trivial trips don't inflate counts."
   ],
   "hows": [
    "Boards (night_rider, early_bird, weekend_warrior, commuter, big_day) bucket validated trips purely by the UTC hour / UTC day-of-week of Trip.start_utc via SQLite strftime, with no conversion to the rider's local time; counts (night/early/bigday/frequent) tally trips and sums (weekend/commuter) total distance, with no minimum-distance or minimum-duration gate.",
    "Five boards bucket validated trips by extracting the hour (strftime %H) or day-of-week (strftime %w) from Trip.start_utc: night_rider (22-04h UTC) and early_bird (05-08h UTC) count trips, weekend_warrior (Sat/Sun) and commuter (Mon-Fri) sum distance, and big_day takes the max trips per calendar UTC date. No timezone conversion is applied.",
    "Counts/sums trips bucketed by SQLite strftime hour and day-of-week directly on Trip.start_utc (night=22-04, early=05-08, weekend=Sat/Sun, commuter=Mon-Fri sum-km, bigday=max trips per UTC date), filtered only on validation_status=='validated'."
   ],
   "reviews": 3,
   "mitigated": None
  },
  {
   "id": "shake",
   "name": "Wobble / shake index",
   "level": 1,
   "cheat": 1,
   "wrong": 3,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "No speed gate inside _max_shake at all (unlike _g_fast/_speed_g): a stationary hand-shake, kick, drop, or parking-lot figure-8 produces the same high gx variance as a 60 km/h shimmy",
    "Std-dev is maximized by spikes, so a single sensor glitch or a +gx/-gx noise pair in the window inflates it far more than a genuine sustained wobble would",
    "The GATE_TIERS gate only requires a long enough overall ride (min_s + min_km); one violent shake at any instant during a qualifying ride sets the trip's shake_index, so the gate doesn't constrain the shake event",
    "gx is an un-normalized raw axis whose units/scaling and physical orientation depend on the source app (DarknessBot vs euc.world vs eucplanet) and phone mount, so values aren't comparable and may capture fore-aft/vertical motion mislabeled as lateral",
    "n>=3 minimum over a sparse-sample window makes std-dev statistically unstable and easily dominated by one outlier",
    "Rewarding the biggest oscillation is a perverse, near-meaningless leaderboard that incentivizes the most violent or most easily-faked behavior"
   ],
   "fixes": [
    "Require corroborated speed above a threshold (e.g. >=20 km/h) for samples to count, normalize/clip gx per source-app and reject single-sample outliers, raise the minimum sample count/rate, and consider dropping the board (the other fakeable IMU lateral/brake boards were already removed).",
    "Gate the shake window on corroborated speed (only count windows where _corrob_speed >= ~20-25 km/h, like g_fast_*), require a minimum sample density (reject windows with n below ~8-10 or effective Hz too low), clamp/reject per-sample gx spikes before computing variance, and ideally require GPS-confirmed movement so a stationary hand-shake cannot register.",
    "Require corroborated speed above a real threshold (e.g. >=15 km/h) within the wobble window itself, clamp/reject gx spikes before the std-dev, demand a minimum sample density (Hz) in the window, and ideally band-pass for shimmy frequency (zero-crossing rate) rather than raw amplitude; or retire it as a public 'max' board since it rewards a safety defect."
   ],
   "hows": [
    "Takes the raw IMU lateral-g column (gx) straight from the source app and reports the largest standard deviation of gx over any ~2s sliding window; higher variance = more 'wobble'. There is zero speed/GPS corroboration and the only filter is the trip-level ride-length/distance gate.",
    "Takes the largest population standard deviation of the raw lateral-g IMU axis (gx) over any ~2s trailing window of samples (min 3 samples), and reports that max as the trip's wobble/shake index. There is no speed gating or GPS corroboration on the window itself; the per-trip leaderboard gate only checks whole-trip duration/distance (smallest tier 300s / 1.5km).",
    "Takes the max standard deviation of the raw lateral-g IMU channel (gx) over any ~2s trailing window in a trip; the trip qualifies only via a whole-ride duration/distance gate, and the window itself has no speed/GPS corroboration."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "longest_ride",
   "name": "Longest single ride (Marathoner)",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "Named 'Marathoner' (implies distance/endurance) but actually measures TIME, not distance: a slow 3 km/h shuffle for 6 hours beats a hard 100 km ride",
    "No min_km / distance gate unlike the GATE_TIERS gated boards; only filter is duration_s>0, so a slow parking-lot loop or downhill coast inflates it freely",
    "coalesce fallback mixes capped moving_s with UNCAPPED duration_s; a trip lacking moving_s (old/crafted log) counts the whole logging session including parked/charging/in-a-bag time",
    "With GPS off, _corrob_speed trusts wheel speed alone, so any wheel reading >2 km/h counts as moving time (stationary spin loophole when GPS absent)",
    "30s gap cap on moving_s still lets a paused-and-resumed or stop-go ride accumulate many short legs into a huge total with little real continuous riding",
    "No sanity ceiling: a multi-day or merged log produces an implausible single-ride time with no upper bound or plausibility check"
   ],
   "fixes": [
    "Add a min_km distance gate (e.g. reuse GATE_TIERS) and drop the duration_s fallback so the board only uses gap-capped moving_s; optionally require GPS-corroborated distance and cap absurd single-ride totals.",
    "Add a real distance floor (e.g. min_km via the gated-board mechanism), drop or separately bucket the duration_s legacy fallback so only true moving_s competes, and require GPS-corroborated distance so stand/freespin time without movement cannot rank.",
    "Drop the duration_s fallback (use moving_s only) and add a gate to this board: require a minimum distance_km and a sane moving avg speed, plus an upper sanity cap on hours per single trip."
   ],
   "hows": [
    "Per rider, takes the max of coalesce(moving_s, duration_s) over validated trips (gated only by duration_s>0) and reports it as ride hours; moving_s is seconds where corroborated speed exceeds 2 km/h with each idle gap capped at 30s, falling back to uncapped wall-clock duration when moving_s is absent.",
    "Per-rider max over validated trips of coalesce(moving_s, duration_s) converted to hours, where moving_s is seconds with corroborated speed >2 km/h (30s gap cap) and the fallback is raw wall-clock duration; only filter is duration_s>0, with no distance gate.",
    "Per-rider maximum, over validated trips with duration_s>0, of coalesce(moving_s, duration_s) converted to hours, where moving_s is rolling seconds with corroborated speed >2 km/h (each idle gap capped at 30s) and duration_s is raw wall-clock first-to-last-sample time used as a fallback."
   ],
   "reviews": 3,
   "mitigated": "Uses moving time."
  },
  {
   "id": "avg_speed",
   "name": "Average speed (Pace)",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 2,
   "issues": [
    "The Pace board (pace_maker) applies NO min distance or min duration gate, unlike the gated boards, so a 10-second cherry-picked downhill or burst trip with a high moving-mean tops the board; you only need one such trip since the board uses MAX over trips.",
    "Mean is over sample COUNT, not time-weighted: dense sampling during fast bursts and sparse sampling while slow inflates the average, and short mostly-high-speed trips score far higher than real sustained pace.",
    "Freespin/wheel-spin protection depends entirely on GPS: when GPS is absent or off, _corrob_speed falls back to raw wheel speed, so a stationary lifted-wheel spin (wheel reads 60, no GPS) feeds 60 into the mean and inflates avg_speed/Pace.",
    "Unlike max_speed, avg_speed never passes through the acceleration-limited _speeds track, so sensor spikes or a spoofed GPS speed inflate it directly with no plausibility clamp.",
    "The 2 km/h floor only strips stationary/walking samples; it does nothing to reject high outliers, downhill coasting, or non-pedaling coast that pads the mean.",
    "Downhill-only or one-way descent routes legitimately yield high averages that misrepresent the rider's actual pace/skill."
   ],
   "fixes": [
    "Compute avg as distance/moving_time (time-weighted) instead of a sample-count mean, gate the Pace board with a real min_km and min_s (e.g. >=3 km and >=600 s), and require GPS corroboration (or clamp to the accel-limited realistic-speed track) so GPS-off wheel-spin/spikes can't inflate it.",
    "Compute avg as distance_km / (moving_s/3600) instead of a sample-mean, add a qualifying gate to pace_maker (e.g. distance_km>=3 and duration>=300s like the gated tiers), and require true GPS corroboration (drop or cap samples lacking gps_speed) plus reuse the acceleration-clamped realistic-speed track to reject freespin/spikes.",
    "Compute avg_speed as distance_km / (moving_s/3600) (true time-weighted moving pace) and add a qualifying gate to the pace board (e.g. min distance and min duration plus a minimum sample_count), matching the anti-gaming gates used on other spikeable boards."
   ],
   "hows": [
    "It is the unweighted arithmetic mean of per-sample corroborated speeds (min of wheel and GPS speed) for all samples above 2 km/h in a trip; the public 'Pace' board then takes each rider's single highest-avg trip.",
    "Per-trip avg_speed is the arithmetic mean of every sample's corroborated speed that exceeds 2 km/h (a sample-mean, not distance/moving-time); the public 'Pace' board (pace_maker) reports each rider's single best trip avg_speed with only a validation_status check and avg_speed>0, no distance/duration gate.",
    "Mean of per-sample corroborated speed (min of wheel and GPS, or raw wheel if no GPS) over only the samples reading >2 km/h; the public 'pace' board then takes each rider's single highest-avg_speed trip with only an avg_speed>0 filter and no min distance/time/sample gate."
   ],
   "reviews": 3,
   "mitigated": "Averaged over moving samples only."
  },
  {
   "id": "geo_counts",
   "name": "Countries / areas explored",
   "level": 2,
   "cheat": 2,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 3,
   "issues": [
    "Name says 'explored'/'countries' but it only counts where trips START; a 200km ride crossing 10 cells or a border counts as a single area/country, while it never credits anything ridden through mid-trip.",
    "Trivially gamed with a GPS spoof / mock-location app: the start point is taken verbatim (one sample) with no corroboration, so a rider can fabricate arbitrary countries and cells without leaving home.",
    "Even honestly, starting rides just on either side of a 0.1-deg cell boundary (or driving the wheel in a car/train to varied start spots) inflates 'areas' without any actual EUC exploration.",
    "No GPS-quality gate on the geo fields: a cold-start/stale first fix or sensor glitch can place the start point in the wrong cell or wrong country, silently adding a bogus area/country.",
    "reverse_geocoder mode=1 returns the NEAREST populated place, so coastal/border/ocean or low-accuracy coordinates can resolve to the wrong country; no distance sanity check.",
    "start_cell silently depends on min(GRID_ZOOMS)=0.1; a config change to grid zooms would redefine 'area' and the whole board, and cell width shrinks with latitude (uneven area sizes worldwide)."
   ],
   "fixes": [
    "Rename to 'distinct start countries/areas' and count cells/countries from the WHOLE corroborated track (sampled points where wheel+GPS agree), not just the first fix; require a minimum GPS accuracy/speed-corroborated fix before assigning country/cell, drop ocean/low-confidence reverse-geocode hits, and dedupe near-boundary cells so a fixed cell-grid stunt can't pad the count.",
    "Gate both boards on a minimum moved distance and require the cell/country to be visited by the actual ridden track (sample path), not just the first fix; for explorer count distinct cells TRAVERSED per trip and de-dupe cheap stop-restart trips (e.g. require min km per trip or merge same-rider trips that are contiguous in time/space). Reject rather than merely flag mock_location for these geo boards and add per-trip single-fake-jump detection.",
    "Geo-tag and gate on the ridden path, not just the start fix: require a minimum corroborated distance/duration per qualifying trip, count distinct countries/cells visited ALONG the GPS track (not just start_cell), drop trips with mock_location OR insufficient GPS-vs-odometer corroboration, and reverse-geocode server-side from real fixes rather than trusting the client mock flag."
   ],
   "hows": [
    "Both boards count distinct values derived ONLY from each validated trip's FIRST GPS sample: globe = distinct ISO country codes (offline reverse_geocoder nearest-place lookup), explorer = distinct 0.1-degree (~11km) grid cells of that same start point. Neither inspects the rest of the track, so they measure distinct trip START locations, not areas actually ridden.",
    "globe = count of distinct Trip.country and explorer = count of distinct start_cell across a rider's validated trips, where both country and start_cell are derived solely from the FIRST GPS sample of each trip (offline reverse-geocode for country, a ~0.1deg/~11km grid floor() for the cell) rather than the path actually ridden. Neither board has a minimum distance/duration gate.",
    "Counts, per rider over validated trips, distinct reverse-geocoded countries (globe) and distinct 0.1-degree start-grid cells (explorer), each derived solely from the FIRST GPS sample (trip start point), not the path actually ridden."
   ],
   "reviews": 3,
   "mitigated": None
  },
  {
   "id": "current",
   "name": "Current 2s / 6s",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 3,
   "wrong_max": 2,
   "issues": [
    "No corroboration at all: unlike speed/g/accel metrics it never checks GPS, speed, or motion - current is high under LOAD not motion, so a stationary wheel pushed/torqued against a wall, freespin, or parking-lot stunt produces a huge current window inside an otherwise-qualifying long ride",
    "current column is trusted verbatim from user-uploaded CSV with no plausibility ceiling (no per-wheel max-amp clamp), unlike speed which has a max_accel clamp - trivially hand-editable / spoofable",
    "No abs() and no sign normalization: s.current is summed raw, so regen/braking-negative firmwares are excluded while drive-negative firmwares silently read ~zero or break; current sign/scale conventions differ across brands so cross-wheel ranking is apples-to-oranges",
    "Window mean uses ssum/sample_count (count-average) not a time-weighted integral, so irregular/bursty sampling skews the result vs a true sustained current",
    "A single BMS/sensor glitch or connector-arc spike inflates the short 2s window heavily (small divisor) with no spike clamp or sanity cap",
    "Often a firmware-computed/estimated value (e.g. derived from power/voltage) rather than a measured shunt current, so it can be a derived-from-a-bigger-value figure"
   ],
   "fixes": [
    "Gate current windows to samples where corroborated speed > a few km/h (require motion), apply abs() plus per-wheel-model sign/scale normalization and an absolute max-amp sanity ceiling, and make _sustained_max time-weighted instead of count-averaged.",
    "Add a minimum-samples-per-window floor (reject windows with <N points / require sampling density) and a speed gate so current only counts while genuinely moving (e.g. corroborated speed above a threshold), plus a per-wheel plausibility cap on amps; keep the instantaneous peak separate as a warning rather than the metric.",
    "Gate the current value itself to samples where corroborated speed > a threshold (mirror _g_fast), use abs() to neutralize regen sign, add an absolute plausibility cap per wheel model, and require a minimum sample density inside the window before counting it."
   ],
   "hows": [
    "_sustained_max slides a 2s (max_sustained_a) or 6s (current_sust_6s) trailing window over per-sample s.current, taking the best window mean (sum divided by sample COUNT, not time), then surfaces the rider/wheel max on a gated board (min duration+distance, per-wheel blocklist). The value is raw current straight from the wheel firmware/BMS with no corroboration.",
    "Takes the max over any ~2s trailing window of the average of the raw CSV 'current' field (battery amps) via _sustained_max; the value is trusted verbatim with no abs(), no speed gate, no GPS corroboration, and no spike/plausibility cap, gated only by a whole-trip duration/distance minimum (smallest tier 300s/1.5km).",
    "_sustained_max takes the highest rolling time-window average (2s for max_sustained_a, 6s for current_sust_6s) of the raw device-reported current channel (s.current) in amps, with no movement, GPS, or physical sanity check; both boards are gated by a min duration+distance ride tier."
   ],
   "reviews": 3,
   "mitigated": "Movement-gated — only counts while genuinely riding (corroborated speed ≥3 km/h)."
  },
  {
   "id": "score",
   "name": "EUC Planet Score",
   "level": 2,
   "cheat": 2,
   "wrong": 2,
   "cheat_max": 2,
   "wrong_max": 2,
   "issues": [
    "The base term distance_km comes from summarize() which prefers the wheel odometer, and odometer_distance_km sums positive deltas with NO GPS corroboration; when GPS is off/spoofed/absent (gps_km<=0) the odometer branch is taken outright, so stationary freespin / wheel-on-a-stand that advances the odometer inflates the dominant term",
    "Even the GPS-fallback path only requires odo >= gps*(1-0.4), so a 40% odometer over-read passes unchallenged and is then raised to dist_exp, magnifying the error",
    "Distance dominates the formula (exponentiated base); the speed and hours terms are only small multipliers (1+x/div), so the board mostly rewards sheer mileage/volume rather than 'champion' performance the name implies",
    "vmax is a per-window max over many trips, so a single anomalous high-speed sample on any one trip permanently boosts the whole window's score; one bad GPS/wheel spike rewards the rider",
    "moving_s falls back to duration_s (wall-clock) for old trips, so a long paused/parked recording can inflate the hours multiplier when moving_s is null",
    "No per-trip gate (min distance/duration) like gated_leaderboard uses; champions runs on raw validated trips, so spikeable distance/speed inputs are not filtered"
   ],
   "fixes": [
    "Use GPS-corroborated distance (or cap odometer distance to a sane multiple of GPS distance) for the score base, require a per-trip min-distance/min-duration gate as the other boards do, and consider capping or down-weighting the exponentiated distance term so the metric reflects balanced performance rather than raw, fakeable mileage.",
    "Rename/reframe as an activity-volume index (not 'champion'), use a percentile/median speed instead of the single max as the multiplier, gate trips that lack GPS corroboration or show implausible distance, and cap each rider's per-window distance contribution to blunt pure volume-farming.",
    "Exclude banned/deleted riders via _excluded_ids, drop the wall-clock duration_s fallback (require real moving_s), and decouple the speed boost from cumulative distance (e.g. score a single best trip, or gate qualifying trips by min_s/min_km) so a lone sprint can't multiply a month of mileage."
   ],
   "hows": [
    "Per rider over a rolling day/week/month window it sums trip distance, sums real moving time, and takes max realistic top speed, then computes distance^dist_exp * (1+top/speed_div) * (1+hours/hours_div); the highest-scoring rider is 'champion'. Distance is the dominant (exponentiated base) term while speed and hours are bounded multiplicative boosts.",
    "A composite leaderboard score = (summed validated distance over a rolling day/week/month window)^dist_exp, multiplied by speed and hours boosters (1 + best max_speed/speed_div)(1 + total moving_hours/hours_div); defaults dist_exp=1, speed_div=100, hours_div=10, so it is essentially 'who logged the most km this period' with modest speed/endurance bonuses.",
    "Per-window (day/week/month) composite that SUMS a rider's validated-trip distance and ride time and takes their single best max_speed, then computes dist^exp * (1 + top/speed_div) * (1 + hours/hours_div) and returns only the top rider; despite the 'champion' name it is a cumulative grind board, not a single-feat peak."
   ],
   "reviews": 3,
   "mitigated": None
  },
  {
   "id": "highspeed_g",
   "name": "G-force while fast (>20/30/40)",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 2,
   "wrong_max": 3,
   "issues": [
    "The g value (s.g) is taken straight from the CSV's 'g-force' column and is never recomputed, bounded, or corroborated against speed/power -- only NaN/inf are dropped. A rider editing the log or an app reporting an inflated channel sets the leaderboard value directly.",
    "abs(s.g) ignores baseline convention: different source apps (DarknessBot, euc.world, eucplanet) report g differently -- some as total magnitude (~1g at rest), some as deviation from gravity. No per-source normalization, so a wheel whose app already bakes in the 1g offset reads ~1g higher than one that doesn't. Cross-rider comparison is apples-to-oranges.",
    "The speed gate uses _corrob_speed but the g value does not, so a genuine sensor SPIKE (curb hit, hard cobblestone, even a near-fall) that happens to land while moving >threshold counts fully as 'g-force while fast'.",
    "_sustained_max degrades to a single-sample value when sampling is sparse: if only one qualifying sample falls inside the ~2s window, its 'average' is just that one reading, defeating the anti-spike intent. Coarse 1Hz logs make this common.",
    "Gated only by trip duration/distance (gated_leaderboard min_s/min_km) -- not by GPS presence. A wheel reporting fast wheel speed with no GPS at all uses _corrob_speed = wheel speed, so a freespin/calibration-off wheel reading 40+ km/h while stationary can satisfy the speed gate, then any IMU jitter is logged as high-speed g.",
    "window is admin-tunable (sustain_secs, default 2s); historical values computed under a different window are not comparable, and a short window keeps it spike-prone."
   ],
   "fixes": [
    "Recompute g server-side from the accelerometer axes (or derive longitudinal g from corroborated-speed change as the existing _speed_g does) instead of trusting the app's g column; normalize the gravity baseline per source app; require GPS-corroborated speed (not wheel-only) for the gate; and clamp to a physically plausible ceiling (e.g. reject sustained-window g above what power/current can produce).",
    "Require IMU corroboration before trusting s.g (e.g. cross-check against current/PWM-derived load, drop trips where g is constant/absent), normalize/document the g axis-and-baseline per source app, and reject windows whose g exceeds a physically plausible sustained ceiling for the corroborated speed.",
    "Subtract the 1g gravity baseline (use net |g|-1 or a high-pass), and require GPS-corroborated (not wheel-only) speed for the gate; consider deriving high-speed longitudinal load from speed change (like _speed_g_band) instead of the raw IMU channel, and add a banded g sanity cap."
   ],
   "hows": [
    "For samples where the GPS-corroborated speed (min of wheel and GPS speed) is at/above 20/30/40 km/h, it takes abs(s.g) -- the raw 'g-force' column the rider's app exported -- and reports the highest ~2s trailing-window average via _sustained_max. The speed GATE is corroborated, but the g VALUE is trusted verbatim from the log.",
    "Takes the best ~2s rolling average of the absolute self-reported 'G-force' CSV column (s.g), but only over samples whose corroborated speed (min of wheel and GPS) is at/above 20/30/40 km/h; surfaced on distance/time-gated boards.",
    "Best 2-second trailing average of the raw CSV g-force channel (abs(s.g)) over samples whose corroborated speed is at/above 20/30/40 km/h. The g value is taken verbatim from the app's accelerometer column with no gravity-baseline removal, and the speed gate falls back to wheel-only speed when GPS is absent."
   ],
   "reviews": 3,
   "mitigated": "Speed-corroborated / interpolated — can't be faked while stationary."
  },
  {
   "id": "gating",
   "name": "Gated-board qualifying tiers",
   "level": 2,
   "cheat": 2,
   "wrong": 2,
   "cheat_max": 2,
   "wrong_max": 2,
   "issues": [
    "Gate uses wall-clock duration_s, not moving_s, so a mostly-parked/idle log that stays recording still clears the min_seconds tier; the gate proves session length, not real riding.",
    "Gate only qualifies the WHOLE trip, not the metric event: one qualifying-length ride lets a single 2s spike/glitch anywhere in it set the board value, so the anti-spike intent is defeated.",
    "distance_km prefers the wheel odometer (separately inflatable: hand-rolling, coasting, quantized jumps) and there is no GPS corroboration of the gate, so distance can be padded without genuine riding.",
    "Thresholds are coarse and the top tier (40km/3600s) is just a normal commute; no requirement that load/g/power occurred during sustained high-speed riding.",
    "GATE_TIERS exposes b/s/m/l suffixes but each registered board binds a single (min_s,min_km) pair, so the tiering is mostly cosmetic and inconsistent across boards.",
    "blocked/masking is per wheel-model metric flag, not per-trip anomaly detection, so an un-flagged wheel with a glitchy channel still poisons its board."
   ],
   "fixes": [
    "Gate on moving_s (and a moving-distance/GPS cross-check), and require the metric's qualifying event to occur within a sustained window of genuine riding rather than anywhere inside a merely long-enough trip.",
    "Gate on moving_s (rolling >2 km/h) instead of duration_s, and validate the spike at the sample level: require the peak window to coincide with corroborated movement (speed > threshold) and use per-trip outlier/anomaly masking, not just model-level blocklists.",
    "Gate on moving_s (real rolling time) not duration_s, and require the peak sample itself to occur while corroborated speed > a floor (per-sample qualification), not just trip-level distance/time; raise the 'b' tier and skip gating for inherently-ambient boards like temp."
   ],
   "hows": [
    "GATE_TIERS defines (suffix, min_seconds, min_km) thresholds and gated_leaderboard only counts a rider's per-trip metric (max/min of a column) when that trip's duration_s and distance_km clear the tier, nulling trips flagged invalid for their wheel model; it ranks the qualifying trips' aggregate.",
    "GATE_TIERS define (min_seconds, min_km) qualifying thresholds; gated_leaderboard only counts a trip's max/min of a spikeable column (power, g, PWM, sag, temp, battery) when Trip.duration_s >= min_s AND Trip.distance_km >= min_km, and nulls columns flagged invalid for the wheel model. It gates per-TRIP, not per-spike.",
    "GATE_TIERS = (min_seconds, min_km) thresholds (b/s/m/l); gated_leaderboard ranks func.max/min of a per-trip column only over trips whose wall-clock duration_s and distance_km clear the tier, with per-(model,metric) admin masking of flagged-bad wheels."
   ],
   "reviews": 3,
   "mitigated": None
  },
  {
   "id": "ride_time",
   "name": "Most ride hours (Steel Legs)",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 2,
   "wrong_max": 2,
   "issues": [
    "No per-trip gate: unlike the gated spike boards, steel_legs just sorts the cumulative total_moving_s, so any number of tiny/spammy validated trips all add up with no min duration/distance filter",
    "Threshold is only 2 km/h (walking pace): pushing/walking the wheel by hand or slow-rolling in circles counts as ride time",
    "_corrob_speed falls back to raw wheel speed when GPS is absent, so a propped-up wheel spinning its tire indoors (no GPS lock) registers as 'moving' indefinitely; only logs that DO have GPS get the min() freespin protection",
    "30s gap cap is generous: repeated short stops (lights, inching forward) each count fully, and a paused/resumed recording can stitch idle time as long as a sample straddles the gap under 30s",
    "Old trips and any trip missing moving_s fall back to full wall-clock duration_s (aggregator/marathoner/champions), counting a phone left recording while parked as ride hours, contradicting the metric's name",
    "Cumulative-sum metric is unbounded and rewards volume/log-keeping diligence (and old fallback history) more than genuine continuous riding; no GPS-distance corroboration that the time reflects real movement"
   ],
   "fixes": [
    "Drop the duration_s fallback (treat missing moving_s as 0 or recompute), require GPS-corroborated movement (or a GPS-distance sanity check) before counting a sample as moving, raise the moving threshold above walking pace, and apply a per-trip min duration/distance gate before summing into the hours board.",
    "Require GPS corroboration for moving_s (drop to wheel-only only with a tight per-sample sanity check, or skip samples lacking GPS), and validate each trip's moving_s against its GPS distance (moving_s capped so implied avg speed is plausible vs gps_distance_km); also stop crediting full duration_s for legacy trips — backfill moving_s or exclude them.",
    "Require per-trip distance corroboration before counting moving_s (e.g. only credit ride time when trip distance_km exceeds a small floor and moving_s is consistent with distance/avg-speed), raise the moving threshold slightly (~3-4 km/h), and drop the duration_s fallback (count 0 ride time when moving_s is unavailable rather than the full session)."
   ],
   "hows": [
    "Per trip, _moving_seconds sums the time between samples whenever the previous corroborated speed (min of wheel and GPS) was >2 km/h, capping any gap at 30s; these per-trip moving_s values are then summed cumulatively into RiderStat.total_moving_s across all validated trips and ranked as hours.",
    "Per trip, _moving_seconds sums elapsed time only when the previous sample's corroborated speed was >2 km/h, capping each inter-sample gap at 30s; these per-trip moving_s values are then summed across ALL validated trips into RiderStat.total_moving_s, shown as cumulative hours with NO gate.",
    "Lifetime sum of per-trip moving_s, where each trip counts seconds whose previous sample had corroborated speed (min of wheel and GPS) > 2 km/h, with each inter-sample gap capped at 30s; shown as total hours. Trips with no moving_s (old/no-GPS) fall back to full wall-clock duration_s."
   ],
   "reviews": 3,
   "mitigated": "Uses real moving time (>2 km/h), not the whole logging session."
  },
  {
   "id": "banded_g",
   "name": "Roll-on / Brake-from-speed G",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 2,
   "wrong_max": 3,
   "issues": [
    "Not a real g: it is |dspeed/dt|*const from the noisy speed channel, never the IMU, so accuracy depends entirely on logger sample rate/quantization.",
    "_corrob_speed = min(wheel,gps) MANUFACTURES brake spikes: a single GPS dropout (tunnel/pocket/urban canyon) drives corrob to ~0 while the wheel is still at 50, recording a phantom 50->0 'braking from speed' with no real deceleration, then a phantom roll-on on recovery.",
    "Band gate only checks the START sample is >=band; it does NOT validate that the speed drop/rise is physically plausible, so any dropout-to-0 from above the band scores maximally.",
    "Sliding-window guard 'right-left>1' means the window can never shrink below 2 points; on sparse/gappy logs dt can far exceed window_s and a coarse, quantized speed step (e.g. 10 km/h jump over 0.4s) yields ~0.7g of pure quantization noise.",
    "No upper plausibility cap (unlike _speeds() which clamps to max_accel); implausible >0.5-0.7g longitudinal values on an EUC are accepted unfiltered.",
    "round(best,3) or None turns a genuine exact-0.0 into None (minor)."
   ],
   "fixes": [
    "Compute the derivative over a fixed-time window with a minimum sample density and reject windows with internal speed gaps/dropouts; cap at a physical max longitudinal g; require both wheel AND gps to agree (or use wheel-only) instead of min(), so a GPS dropout cannot fabricate a braking/accel spike; cross-check against IMU gy when available.",
    "Apply the same believable-acceleration clamp used in _speeds (reject per-sample deltas implying > ~max_accel km/h/s as freespin/spikes), require both window endpoints (not just the left) to be plausible, drop windows built from a sparse >1.x*window_s gap, and require GPS corroboration (or flag GPS-less trips) before counting band g.",
    "Apply the same accel-clamp/freespin rejection used by _speeds() before computing band g, require the speed to stay >= band across the whole window (not just the start), skip windows whose dt exceeds ~1.5x window_s (real gap) and require a minimum sample density / GPS corroboration; clamp to a physically plausible max g and discard single-sample step deltas."
   ],
   "hows": [
    "From the corroborated wheel/GPS speed (min of the two), it takes |delta-speed / delta-t| over a ~1s sliding window and converts km/h-per-s to g, counting only windows whose START sample is at/above the band (30 or 50 km/h) for roll-on accel and braking-from-speed. It is a unit-converted speed derivative, not a measured IMU g.",
    "Sliding ~1s window over corroborated speed (min of wheel and GPS, falling back to raw wheel when no GPS); for any window whose START sample is >= the band (30/50 km/h) it computes |delta-speed/dt| converted to g, taking the max rising delta as roll-on accel_g and max falling delta as brake_g.",
    "_speed_g_band scans a ~1s sliding window over per-sample corroborated speed and reports the largest speed-derived longitudinal g (accel/brake) for windows whose START sample is >= the band (30 or 50 km/h), converted via km/h-per-s -> g; the per-trip max feeds duration/distance-gated boards."
   ],
   "reviews": 3,
   "mitigated": "Speed-corroborated / interpolated — can't be faked while stationary."
  },
  {
   "id": "sprints",
   "name": "Sprints 0->40 / 0->60 / 0->100",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 2,
   "wrong_max": 2,
   "issues": [
    "GPS corroboration is OPTIONAL: _corrob_speed falls back to raw wheel speed when gps_speed is None (GPS off/absent/old logs), so the 'can't be faked' claim is false for any log without a GPS speed channel",
    "No anti-freespin guard: unlike _speeds(), _fastest_0_40 reads raw _corrob_speed with no accel-clamp/freespin_margin, so a stationary wheel-spin or lifted wheel that ramps wheel speed to 60/100 km/h is counted as a real sprint (when GPS is missing)",
    "Linear interpolation fabricates sub-second precision from sparse/1Hz sampling: a wheel that blows through the target between two readings gets an invented crossing time, making the value sampling-rate/noise driven",
    "min_s floor (1.0-1.5s) is a gameable boundary: with coarse sampling a 2-sample jump lands at exactly the floor and passes, and fastest entries cluster at the floor",
    "0->100 km/h is essentially physically impossible for legitimate consumer EUCs, so any 0->100 entry is almost certainly a freespin/spike/no-GPS artifact rather than a real launch",
    "GPS speed lags hard acceleration (~1Hz), so min(wheel,gps) can push the target crossing late and inflate honest riders' sprint times when GPS is present"
   ],
   "fixes": [
    "Require GPS corroboration for the sprint to count (skip the metric entirely, or require gps_speed near target at the crossing) instead of silently falling back to raw wheel speed; reuse the accel-clamp/freespin rejection from _speeds(); reject sprints where the crossing is interpolated across a sample gap larger than ~1-2s; cap the believable target (drop 0->100, or flag it) and require a minimum sample density inside the launch window.",
    "Reuse the freespin accel-cap: require the corroborated speed to rise within max_accel and reject the launch if max_freespin fired in that window; require a real GPS-speed channel (or wheel-current/power signature) for 0->60 and 0->100, and interpolate the start edge too.",
    "Require an independent GPS-speed crossing of the target (not just min()), reject sprints whose implied sustained accel exceeds a physical cap (raise min_s to a realistic floor per target, e.g. >=2.5s to 60, >=5s to 100), and verify GPS distance actually accumulated across the sprint window before counting it."
   ],
   "hows": [
    "_fastest_0_40 returns the shortest time (linearly interpolated between the two bracketing samples) from a near-stop (<=2 km/h) up to a target speed (40/60/100 km/h), using _corrob_speed = min(wheel,gps) when GPS exists and raw wheel speed otherwise, bounded by min_s/max_s floors and ceilings. Lower is better.",
    "For each launch from <=2 km/h, _fastest_0_40 finds the first sample reaching the target (40/60/100 km/h) using _corrob_speed, linearly interpolates the crossing time between the two bracketing samples, and keeps the shortest elapsed time within a min/max window (0-40: 1.5-20s; 0-60: 1.0-40s; 0-100: 1.0-60s). Lower is better.",
    "Shortest interpolated time from a near-stop (<=2 km/h) up to 40/60/100 km/h using _corrob_speed (min of wheel & GPS speed, or wheel-only when no GPS), bounded by min_s/max_s floors and surfaced on gated min boards (lower is better)."
   ],
   "reviews": 3,
   "mitigated": "Speed-corroborated / interpolated — can't be faked while stationary."
  },
  {
   "id": "top_speed",
   "name": "Top speed",
   "level": 1,
   "cheat": 1,
   "wrong": 2,
   "cheat_max": 2,
   "wrong_max": 2,
   "issues": [
    "GPS corroboration is optional, not required: with no GPS samples _corrob_speed falls back to raw wheel speed, so firmware-inflated, mis-calibrated wheel-size, or spoofed wheel speed passes through as a 'realistic' top speed with no ground-truth check",
    "Accel cap only blocks instantaneous spikes; a lifted wheel feathered so speed climbs <=20 km/h/s ramps to 100+ km/h and lands on the realistic board, not freespin, because the raw peak never jumps >5 km/h above the believable track",
    "Public speed_leaderboard reads RiderStat.best_speed (max of Trip.max_speed) directly via _board with NO min_distance/min_duration gate, unlike power/g boards; a 3s near-zero-distance stunt trip qualifies",
    "min(wheel,GPS) under-reports honest riders whose GPS lags under load / in tunnels, yet a GPS-disabled log is taken fully at face value -- inconsistent and effectively rewards turning GPS off if the wheel over-reads",
    "max_accel cap is a single admin-global value: a genuinely hard-launching wheel can have legit acceleration rejected while the same cap is trivially satisfiable by a slow lifted-wheel ramp",
    "Corroboration is optional: if the log has no gps_speed column (or per-sample nulls), _corrob_speed silently returns raw wheel/firmware speed with NO GPS check; the 'acceleration-corroborated vs freespin' guarantee degrades to wheel-speed-with-an-accel-cap, which is fully self-reported and editable."
   ],
   "fixes": [
    "Require GPS corroboration (or wheel-model speed sanity bounds) for the top-speed value, gate the public speed board on min distance/duration like the spikeable boards, and prefer a per-wheel-model accel cap rather than one global constant.",
    "Require real GPS corroboration to credit max_speed (fall back to a clearly-labeled 'unverified/wheel-only' tier, or use ext_gps_speed), add an absolute ceiling and a per-wheel-model rated-speed sanity cap, and treat sustained GPS~0 while wheel-speed>0 as freespin even on a smooth ramp.",
    "Require GPS corroboration (or PWM/current load) for the top-speed value rather than falling back to bare wheel speed; tighten max_accel toward a realistic ~8-10 km/h/s, add an absolute speed ceiling, and gate the public board on min distance/duration plus a short sustain window."
   ],
   "hows": [
    "Reports the peak of an acceleration-limited speed track: per-sample speed is the lower of wheel and GPS speed (or raw wheel speed when no GPS exists), and rises are capped at max_accel (20 km/h/s) so instantaneous spikes are diverted to a separate freespin board only if the raw peak beats the believable track by >5 km/h. The public speed board takes the max of this per-trip value with no distance/time gate.",
    "Walks samples in time order keeping a 'plausible' speed that may only rise at <=max_accel (20 km/h/s, ~0.57g) but may fall freely; max_speed is the peak of that accel-limited track of the per-sample corroborated speed (min of wheel and GPS speed, or wheel alone if no GPS). A raw wheel peak exceeding the realistic peak by >5 km/h is split off as max_freespin instead of counting as speed.",
    "max_speed is the peak of an acceleration-limited speed track (rises capped at max_accel=20 km/h/s, decels free) built from _corrob_speed, the per-sample min of wheel and GPS speed (wheel-only when GPS is absent); raw peaks more than freespin_margin=5 km/h above that believable peak are diverted to max_freespin instead."
   ],
   "reviews": 3,
   "mitigated": "Uses the acceleration-corroborated realistic speed (freespin excluded)."
  }
 ]
}
