# Results: RoboNine STS32XX firmware v9 (conditions RL and RE)

Image: `firmware.enc`, 25,744 bytes, SHA-256 `71b406a2166a9dec8c02b9daa3c67648c52f045ef03c9cfb80134a0bfa68e003`, flashed from the bench on 2026-11-26 11:32
(a first flash on 2026-11-25 16:02 was undone by the incident, see 06). The bench reads the firmware as "RoboNine v9.0 (fw 3.10)" and the extension check answers **ARMC 9.0**.
Same servo, jig, lever (50 mm) and supply as condition F.

## RL (RoboNine, legacy commands): trials only

| Run | Cycles | Result |
|---|---|---|
| 11-25 16:03, 16:04 (first flash, target 1641) | 3 + 3 | all arrivals flagged stalled, encoder error 4 counts |
| 11-26 11:32 (target 1595) | 3 | all 3 flagged stalled, encoder error 4 counts, gauge 2.04 mm |

Same behaviour as the Feetech first run (stop at +4 against a 3-count tolerance): in legacy mode RoboNine's firmware seems to behave like the stock one. **No 100-cycle RL run exists yet.**

## RE (RoboNine controller, extension mode): one long run

Run `2026-11-26_113458_T4.1a_robonine-v9.0-fw-3.10_extension`: **1,305 cycles, 5 h 20 min** (11:34-16:55, stopped), target 1595, limits 1565..3147, wobble 10 × 62, config = defaults.

| Quantity | Value |
|---|---|
| Arrivals | 1,303 ok, **0 stalled** |
| Encoder error (position − target) | **0.002 ± 0.083 counts** (min −1, max +2) in extension mode |
| Gauge mean / median | 2.531 / 2.53 mm (gauge zero differs from the F runs) |
| Gauge SD (plain) | **0.0424 mm** (87 arcsec = 0.024°), range 0.74 mm (2.24-2.98) |
| Gauge SD (robust, 1.4826 × MAD) | **0.0148 mm**, the same as F |
| Outliers beyond ±0.1 mm | **21 of 1,299 (1.6 %)**: 13 low (−0.20 to −0.29 mm) and 8 high (+0.10 to +0.45 mm) |
| Where the outliers are | 7 before cycle 900 (the first at 12:45), then 14 between cycles 1063 and 1300 (after about 15:56); 38-40 °C |
| Drift | −0.015 mm per 1,000 cycles; fifths 2.533, 2.543, 2.528, 2.532, 2.518 |
| Temperature / voltage | 36-40 °C, 12 V (no spikes in this run) |
| Move time | 1,174 ms mean (same as F: the travel dominates) |
| Hold current | not available in extension mode (the bench logs a raw value, not mA) |

The outlier readings are real digits (the vision model saw e.g. "230", "298", "26.7"), and the encoder error on those same cycles was 0, ±1, 2. So the output moved by
**0.2-0.45 mm (0.25-0.5° at 50 mm)** while the encoder said "on target": the size of the datasheet backlash. See OBS-003 / OBS-004 in the log.

## Trials in extension mode

| Run | Result |
|---|---|
| 11-25 16:05 (first flash) | 3 cycles, no stalls, encoder error 0.5, gauge sd 0.085 mm (2 samples) |
| 11-26 11:33 | 3 cycles, no stalls, encoder error 0 |
| 11-26 17:44 | 3 cycles after a camera change (index 1) and new positions (target 1891, limits 1861..2109): 2 stalled arrivals, gauge 5.395 mm (not comparable) |

## Not measured yet on RL / RE

T3.1 (the dead zone), T4.2b, T3, hold jitter, a 100-cycle RL run, F2 (back to factory) as a control.
