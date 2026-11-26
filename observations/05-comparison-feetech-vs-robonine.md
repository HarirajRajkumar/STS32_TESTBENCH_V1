# Comparison: Feetech factory (F) vs RoboNine v9 (RL / RE), status 2026-11-26

**One servo, one lever (50 mm), one run per condition so far. Treat everything as preliminary.**

## Matrix

| Measure | F: Feetech factory 3.10 | RL: RoboNine legacy | RE: RoboNine extension | RoboNine article (stock Feetech) |
|---|---|---|---|---|
| Steady position error (encoder, after arrival) | **+3.00 counts** (0.26°) on 99.8 % of 1,406 cycles | +4 counts (3-cycle trials, flagged stalled) | **0.00 ± 0.08 counts** (1,299 cycles) | repeatability ≈ 2 counts mean absolute deviation |
| Command dead zone (T3.1) | **not run** | not run | not run | **10 counts** (0.88°) |
| Hysteresis from below vs above (T3.1 B / T4.2b) | not run | not run | not run | not reported |
| Hold jitter (T3.1 C) | not run | not run | not run | "visible vibration with the arm extended" |
| Gauge spread, typical (robust SD, 0.01 mm steps) | 0.0148 mm | not run | 0.0148 mm | ±0.3 mm at a 10 cm lever (different method) |
| Gauge SD, plain | 0.0083-0.0167 mm (n 269-1,406) | not run | **0.0424 mm** (n 1,299) | n/a |
| Large gauge excursions (> 0.1 mm) | 3 of 1,406 (0.2 %), all three flagged stalled | not run | **21 of 1,299 (1.6 %)**, none flagged, encoder error ~0 | n/a |
| Stalls / not-arrived | 3 of 1,406 (+ 89 of 97 in the first run without wobble) | 3 of 3 (trials) | 0 of 1,303 | n/a |
| Move time to target | 1,134 ms | ~1,500 ms (stalled, timeout-limited) | 1,174 ms | n/a |
| Drift of the mean | −0.018 mm / 1,000 cycles | not run | −0.015 mm / 1,000 cycles | n/a |
| Temperature (small repeated move) | 35-37 °C at 5 h (+ sensor spikes) | not run | 36-40 °C at 5 h | 48 °C (1 kg at 15 cm), 71 °C oscillating ±90° |
| Backlash / free play (T3) | not run | not run | not run | **0.87°** (0.57° on another unit); datasheet ≤ 0.5° |

## Preliminary findings

1. **The extension mode removes the steady +3-count offset** (0.00 vs +3.00 counts). The stock-firmware offset equals its arrival tolerance and looks like the dead zone acting on the approach from above. T3.1 will measure it directly. (OBS-002)
2. **The typical gauge spread is the same in F and RE** (robust SD 0.0148 mm = one or two 0.01 mm gauge steps). On the part of the distribution the gauge can resolve, the firmwares do not differ. (OBS-001)
3. **RE shows more large excursions** of the output: 1.6 % of cycles move the output 0.2-0.45 mm (0.25-0.5°) while the encoder reads zero error; F had 0.2 %. Most appear in the last third of the run. If the encoder sits before the gearbox, as RoboNine's article states, play in the gears is invisible to it, and
   this is the signature of gear play being taken up on one side or the other. It could also be a loose part of the jig, or camera / gauge effects. The frames of those cycles should be checked. (OBS-003, OBS-004)
4. **In legacy mode RoboNine's firmware behaves like the stock one** in the only data we have (3-cycle trials: stop at +4, flagged stalled). Needs a 100-cycle RL run. (OBS-006)
5. **No test yet answers the article's two headline claims** (backlash 0.87°, dead zone 10 counts): T3 and T3.1 have not been run.

## Caveats that stop a clean comparison today

* Different gauge zero (1.06 mm on F days, 2.53 mm on RE day), different target (1615 vs 1595) and limits; the gauge and camera were re-set between the conditions.
* One run each, on different days; the F long run has no matching RL / F2 control.
* The gauge reads in 0.01 mm steps (about 0.011° at 50 mm), vision-read, no human check of every frame.
* The camera was changed (index 0 → 1) on the last RE trial, so later data is not comparable with the earlier runs unless the gauge box is redrawn and checked.

## What is needed to decide (in this order)

1. **T3.1 Auto test** in RE now, then RL, F, F2 (about 5 minutes each, no gauge). Answers: dead zone size, hysteresis, jitter, per firmware.
2. **T4.2b** (up/down) in the same conditions: the lost motion at the target, with the gauge.
3. **100-cycle T4.1a in RL and F2** with the same target, limits, gauge zero and camera as F. Inspect the frames of every excursion > 0.1 mm.
4. **T3 push test once** with the beam (near vertical, fixed torque) to put a number on gearbox play: the same on every firmware, so it only needs doing once per set-up.
5. Repeat the best condition on the beam for jitter with an extended arm (the article's observation).

## Decision rule (write the result here when the data is in)

RoboNine v9 is "better" than the stock firmware if, in the same set-up: the T3.1 dead zone is smaller, the T4.2b hysteresis is not larger, the hold jitter is not larger, the T4.1a excursions (> 0.1 mm) are not more frequent, and nothing breaks (stalls, faults, temperature). Mixed results are reported as mixed, per measure.
