# Falls / Cutouts and Free spins: detection rules for eucviewer

Status: validated on real rides, 2026-10-14. Supersedes "EUC Cutout Detection".
Reference implementation: `ingest/anomalies.py` in eucstats (`detect()`).
The rules below are the ones eucstats now runs in production ingest.

## 0. What changed, in one paragraph

The original rule was "the wheel is turning and the GPS is not, so the wheel is unloaded".
That rule does not survive contact with real logs: of 495 candidates it raised across the
reference library, **only 8% were genuinely unloaded**. The other 92% were riders carrying on
down the road through a GPS dropout of about two seconds. Everything below exists to separate
those two, and almost every guard was added because a specific real ride was being libelled
by its absence.

A detector that reports a lot of falls is not sensitive, it is broken. Across the rides we can
still replay, the honest answer is **one fall and eight free spins**.

## 1. Naming, and what is publishable

| Internal | Public name | Published? |
|---|---|---|
| `fall`  | **Falls / Cutouts** | yes |
| `spin`  | **Free spins** | yes |
| `spike` | -                   | **no**, diagnostic only |
| `glitch`| -                   | **no**, diagnostic only |

`spike` and `glitch` are the detector saying "something in this log is off" to *you*, not a
claim about the rider. Do not put them on a board or a rider's profile. In our library 1,347
spikes and 93 glitches sit behind 9 publishable events, so surfacing them would drown the
signal and accuse people of nothing in particular.

## 2. The physical idea

A free spin, a pickup test and a cutout are **the same event in telemetry**: the wheel turning
with nothing loading the motor. Only the context either side separates them.

```
unloaded spin while travelling   -> FALL       (a cutout lands here)
unloaded spin from a standstill  -> FREE SPIN  (pickup test, hand spin, bench check)
```

So never hunt for a fall directly. Find the unloaded spin, then read the context.

A **collision** is the exact inverse and needs its own branch (section 7): there the wheel
stops and the ground keeps moving, and the motor loads *harder* than anywhere else in the ride.

## 3. Constants

```
free_spin_kmh      12.0   wheel km/h before a reported standstill is even a candidate
gps_stopped_kmh     2.0   GPS speed treated as stationary
track_moving_kmh    3.0   ground speed from the TRACK that contradicts a reported stop
accel_impossible    6.0   m/s^2 no loaded wheel produces (a real EUC peaks near 3)
max_accel          20.0   km/h per second a loaded wheel can gain
freespin_margin     5.0   km/h the wheel must beat the believable track by
motor_active_a      1.0   amps that count as the motor doing work (watts track at x100)
load_trail          3     samples PAST the event the motor must also be idle for
min_fall_len        3      \  a fall must persist: one sample is a sensor blip,
min_fall_s          2.0    /  and it must cover real time, whatever the logging rate
min_event_len       3      \  same floors for a free spin
min_event_s         2.0    /
max_gap_s           5.0   a longer logging gap makes every rate meaningless
impact_min_kmh     15.0   the rider must have been travelling for a crash to be a crash
impact_ground_kmh   8.0   ground speed still showing when the wheel stopped: his momentum
impact_g            2.5   g that counts as hitting something
impact_amps        40.0   fallback current surge, only where there is no g channel
impact_stop_s       5.0   seconds the wheel has to go from travelling to nothing
impact_hold_s       5.0   seconds everything must then STAY stopped
CHANNEL_JITTER      0.05  amps of VARIATION that prove the current channel is reporting
SURROUND            8     samples either side for "is the current channel alive"
CONTEXT            10     samples either side for "was he travelling"
RUNUP               4     samples of run-up that decide whether a spin began from rest
MOVING_KMH          3.0   median ground speed that counts as travelling
```

`free_spin_kmh` is the single strongest lever on false positives. At 5 km/h, walking a wheel
off a lift gets reported.

## 4. Per-trip probes, before anything else

**Does this log have a motor channel at all?** `any(|current| > 0 or |power| > 0)`.

Older exports stop at GPS speed. Reading a missing column as zero makes every "was the motor
working?" test answer no, and the detector then concludes the wheel was never loaded for the
whole ride: in our library that turned 2,521 events into 2,521 glitches and reported zero
falls on a dataset containing a real one. **122 of 789 trips carry no current at all**, so
every load test must degrade to "unknown", never to "idle". (That 122 of 789 is from an
earlier, larger survey, before we started evicting raw uploads to save disk; every trip in the
307 we can replay today happens to carry both current and GPS speed. Do not read that as the
channels always being there.)

**Does this log carry positions?** `any(lat is not None and lon is not None)`. If it does,
a sample without a position cannot be used as evidence of a standstill.

## 5. Deriving ground truth

### 5.1 The track, not the speedometer

Compute ground speed from the **positions**, over +/-2 fixed samples, rejecting windows
spanning more than 8 s. Where the reported GPS speed and the track disagree, the track is
right: on one trip the speedometer sat at 0.5 km/h for four seconds while the latitude climbed
steadily, eleven metres of real ground during a reported standstill. A speed field can stall
or round to zero; a position that keeps advancing cannot.

Known limit, and it matters: this reads a false zero when the smoothing window fits inside one
**repeated** fix, because it then divides no distance by four real seconds. So the track must
never be the only thing standing between an ordinary ride and a reported fall (section 6.4).

### 5.2 "Stopped" means the ground stopped, not the receiver gave up

A sample counts as stopped only when **all** of:

- `gps_speed` is present and `< gps_stopped_kmh`. An absent `gps_speed` is **unknown, never
  zero**. `(gps_speed or 0)` calls every log without a GPS channel permanently stopped.
- the sample carries a position, if the log carries positions anywhere. A GPS speed of exactly
  0.0 is what a log writes both when the rider stands still and when the fix is gone. On one
  trip the zero readings had no latitude at all: the wheel was cruising at 15 km/h through a
  dropout, and reading that as a standstill made it a fall.
- the track, where known, is `< track_moving_kmh`.

### 5.3 The believable speed track

Walk the ride forwards letting speed rise only as fast as a loaded wheel can:
ground is `min(wheel, gps)`, and the ceiling rises by at most `max_accel` km/h per second
(clamped at `max_gap_s` across gaps). `runaway` is how far the wheel beat that ceiling.

This is the free-spin test that **needs no current channel**, which is the point: lift a wheel
and the balance algorithm spins it to 40 or 50 km/h in a second or two, and nothing with a
rider aboard does that.

## 6. Falls and free spins

### 6.1 Flag (any one suffices)

For each adjacent pair, skipping `dt <= 0` or `dt > max_gap_s`:

- **(a)** `wheel > free_spin_kmh` and the sample is stopped per 5.2. A **candidate only**.
- **(b)** `|Δspeed| / dt > accel_impossible` with `max(speeds) > 10` km/h. **Both directions.**
  An EUC pulls up at roughly half a g, so 30 km/h to nothing in a second is not a rider
  stopping, it is a rider hitting something. Testing only the acceleration half left the whole
  crash-into-a-kerb shape invisible on any log without current.
- **(c)** `wheel >= 25` km/h with no current within +/-2 samples, **only if** the log has a
  motor channel. A logger writing fiction, typically during shutdown.

### 6.2 Group into events

Tolerate one clean sample inside an event; **two clean samples in a row end it.**

### 6.3 Is the current channel alive here?

By **VARIATION, not magnitude**: over the event plus `SURROUND` either side, take `|current|`
(or `|power|/100` if fewer than 4 current samples) and require
`max - min > CHANNEL_JITTER`. Fewer than 4 values, or no variation, means the channel is
dead -> **glitch**, and stop.

The question this replaces, "did the motor draw a real amp nearby?", has the wrong answer for
the very thing we most want to find: a wheel spun up on its stand is idle before and idle
after *by definition*, so every free spin classified itself as a logger glitch. That scored
**0 detections out of 500 trips with a real free spin spliced in**. Meanwhile a dead BLE feed
reports the same number forever: on one trip -0.9 A for nineteen thousand samples, on another
exactly 0.0 while the rider did 75 km/h.

When the log has no motor channel at all, treat the channel as alive (so an absent column can
never manufacture a glitch) and let the speed tests decide alone.

### 6.4 Was the wheel unloaded?

```
held          = MEDIAN of the event's wheel speeds
peak          = MAX of them
peak_accel    = the most violent |Δspeed|/dt inside the event
runaway       = max(wheel - believable) across the event

impossible_speed = peak >= free_spin_kmh and held >= free_spin_kmh
                   and (runaway > freespin_margin or peak_accel >= accel_impossible)

load           = MEDIAN |current| over the event AND its next `load_trail` samples
current_agrees = (log has no motor channel) or load < motor_active_a

unloaded       = impossible_speed and current_agrees
```

Four things here are load-bearing, and each one has a ride behind it:

**Speed is the base test and always applies**, so a log with no current can still be judged.
Where current exists it **corroborates rather than substitutes**: both must agree. A wheel
whose speed ran away while the motor pulled 9 A had a rider on it; a wheel drawing nothing
whose speed never did anything impossible was only coasting.

**`held`, the median, not just the peak.** One trip read 26.1, 2.7, 18.6, 2.6 km/h in four
seconds with 19.7 A on the first: a rider at walking pace on a link dropping packets. A peak
alone cannot tell that from a free spin. The median can.

**The load is a median, not a count of idle-looking samples.** Regen braking sweeps the current
from -16 A up through zero to +7 A, so single samples sit near nothing on the way past.
Counting those made hard braking look like a wheel with nobody on it.

**The load window runs PAST the event, and the run-up is never included.** This is the newest
rule and the one most likely to be missing from your implementation. A median alone still
fails when the whole event lands inside the zero crossing: two ordinary rides read
`0.0, 0.7, 1.0 A` and `-3.0, -0.8, -0.1 A` across every sample they contained, at 25 and
13 km/h, and both were reported as falls. What separates them from a cutout is what happens
**next**: a cutout leaves the motor off, so the wheel freewheels or lies still and the current
stays near nothing, while a braking rider is back at -12 A a second later. The asymmetry is
deliberate - a real cutout is loaded right up to the moment it lets go, so requiring the
run-up to be idle too would throw every genuine one away.

### 6.5 Read the context

```
before    = travelling over the CONTEXT samples before the event   (True / False / UNKNOWN)
after     = travelling over the CONTEXT samples after it           (True / False / UNKNOWN)
ended     = at rest over the CONTEXT samples after it
from_rest = median(previous RUNUP wheel speeds) < MOVING_KMH and before is not True
```

`before` and `after` are **tri-state**. Returning UNKNOWN rather than False is what stops a
trip with neither GPS nor current from defaulting into a verdict. Use **medians**: one spike
must not decide whether somebody was riding. Ask the track first, the GPS speed second, the
current last.

`from_rest` reads the immediate run-up, not a ten-sample median: a rider who stops, steps off
and picks the wheel up has a window straddling both, and a median of riding-then-stopped lands
halfway between and answers neither question. **Both** the wheel and the ground must have been
still - on one trip the wheel column read 0.0 only because the link was down while the rider
did 70 km/h by GPS, and taking the wheel's word for it turned a road ride into a 78.9 km/h
free spin.

### 6.6 Decide, in this order

```
if not channel_alive:                                  -> GLITCH
elif unloaded and before is True
     and (after is False or ended)
     and len >= min_fall_len and span >= min_fall_s:    -> FALL
elif unloaded and from_rest
     and len >= min_event_len and span >= min_event_s:  -> FREE SPIN
else:                                                   -> SPIKE
```

**Falls are tested first**, so a crash can never fall through to the harmless board. Splicing
crashes into real rides showed why: where the context could not establish that the rider had
been travelling, a 28 km/h fall was being filed as a free spin.

**The asymmetry in the endings is deliberate.** A fall must be positively confirmed by a stop;
a free spin may end in unknown territory, which is common at the end of a log. A fall ends the
ride - that is the whole difference. A wheel spun up on a stand is put back down and life goes
on; a rider on the ground stays there.

**No condition on what happens after a free spin.** The commonest one there is goes stop, pick
the wheel up, spin it, set it down, ride on, and requiring the rider *not* to be moving
afterwards ruled out exactly that.

**Both floors matter.** `min_*_len` without `min_*_s` says nothing at 10 Hz; `min_*_s` without
`min_*_len` lets two samples two seconds apart through. Every single-sample event in our
library was noise: a rolling stop, a braking transient.

## 7. Impact falls: the other way a rider goes down

A cutout unloads the motor and the wheel runs away. A collision does the opposite: whatever
was hit stops the wheel, the motor slams to its limit holding the rider up, and his body
carries on forward without it. Requiring an unloaded motor missed the only confirmed crash in
the library entirely, **because a crash loads the motor harder than anything else in the ride**.
The evidence that proved it was a crash was being used to dismiss it.

Walk every sample where the wheel first drops below `MOVING_KMH`, and require all of:

1. **It arrived from real speed, fast.** Some sample within `impact_stop_s` beforehand read
   `>= impact_min_kmh`.
2. **The rider was still going.** Read the **reported** `gps_speed` at that moment, not the
   smoothed track: smoothing lags, so on the track every ordinary stop still shows 8 km/h when
   the wheel reaches nothing. That one substitution was the difference between **1 fall across
   the library and 108 of them**.
3. **Something actually hit.** Where the log has an accelerometer, **it decides**:
   `|g| >= impact_g` within +/-3 samples. Accepting a current surge instead gave 11 falls,
   nearly all to one rider on the most powerful wheel, because 40 A is an ordinary launch
   there. Current is the fallback **only** for a log with no g channel, and then it must beat
   `max(impact_amps, 3 x the trip's own 95th-percentile draw)` - a surge well past what that
   trip does elsewhere, not a big absolute number.
4. **Then everything stops, and stays stopped** for `impact_hold_s`. This is what separates a
   crash from a Bluetooth link dropping out: on one trip the first three held and this did not,
   because the wheel read zero while the rider carried on down the road at 25 km/h. He rode
   away from it.

One crash counts once: suppress repeats within 30 s.

### A warning about the g channel

Treat a lone high-g sample as nothing. One trip showed 4.18 g on a single sample with ordinary
0.2 g deceleration either side and GPS lag fully explaining the divergence; we called it a
crash and the rider replied that he had not ridden that day. **Require consecutive elevated
samples**, and when in doubt ask the rider before showing it. The one event we still report
has 4.90, then 3.11, then 1.28 g, the wheel going 47.9 -> 5.4 km/h while GPS still read 30.3,
PWM collapsing to 0.7%, and the wheel then reading 0.0 for three minutes. Even that one we
have not published without asking him.

## 8. Validation, and what to expect

Surveyed 2026-10-14 over 307 trips whose raw telemetry we can still replay (of 1,299 stored):

| | count |
|---|---|
| **Falls / Cutouts** | **1**, pending rider confirmation |
| **Free spins** | **8** |
| spikes (not published) | 1,347 |
| glitches (not published) | 93 |

Events rejected, by the first test each failed - the shape of the problem in one table:

| reason | n |
|---|---|
| speed looked impossible but the motor was pulling: a rider was on it | 1,167 |
| speed was believable for a loaded wheel | 131 |
| current channel dead nearby -> glitch | 93 |
| fall- or spin-shaped but under the length or duration floor | 30 |
| travelling before AND after: rode straight through it | 18 |
| stopped, then moving off: a normal launch | 9 |

**Calibrate against that first row.** Four fifths of everything that looks like a free spin is
a rider accelerating hard. If your counts are an order of magnitude above ours, the load test
or the duration floors are the first place to look.

Known false-positive classes, all of which cost us real reports:

1. Riding through a ~2 s GPS dropout (92% of raw wheel-vs-GPS candidates).
2. Hard launches on powerful wheels read as free spins when current is ignored.
3. Regen braking through zero current read as an unloaded wheel - both the single-sample case
   and the whole-event case of 6.4.
4. A stale GPS fix, repeated for several samples, read as a standstill.
5. A dropped BLE link read as the wheel stopping (the rider rides on by GPS).
6. A wheel column copied from GPS, and a GPS speed column stuck at one value.
7. A lone accelerometer spike read as an impact.

## 9. Testing advice, learned the hard way

**Build fixtures from real telemetry dumps, verbatim.** Reduced synthetic fixtures passed
repeatedly for the wrong reasons, and every rule in section 6 was at some point defended by a
test that would have passed without it. One fixture invented plausible positions as a straight
line, so the track never stopped, which was the one thing that test turned on.

**Specificity and recall need separate suites.** Survey real rides and hand-inspect every
single claim for specificity; splice known events into real rides for recall. We found a
detector scoring 0/500 on recall while looking perfect on a survey, because the thing it could
not see produced no false positives either.

**Trace, do not re-derive.** Have the detector emit its evidence for every event and have the
reporting tool read that, rather than recomputing the decision. Two copies of the rules drift,
and the copy doing the explaining is the one nobody tests: ours printed nothing at all for a
trip while the detector was reporting a fall on it.

## 10. Questions, and the one thing we would change

The impact branch (section 7) rests on a single unconfirmed event. If you have crash logs with
a known ground truth we would rather calibrate against those than keep it on this footing;
dropping the branch entirely is a defensible choice until then, and costs one report.
