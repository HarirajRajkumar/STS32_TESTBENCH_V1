# Results: Feetech factory firmware 3.10 (condition F), legacy control

All runs: T4.1a, same direction (approach from above), 50 mm lever, 12 V, speed 1000 counts/s, accel 5000, dwell 1.5 s, tolerance 3 counts,
gauge read by camera + Qwen at 0.01 mm. Camera index 0, rotated 180°. Statistics skip cycle 1.

| Run (start) | Cycles | Target / limits | Wobble | n used | Gauge mean | **SD** | Range | Stalls | Encoder error |
|---|---|---|---|---|---|---|---|---|---|
| 11-25 15:17 | 100 | 1757 / 1727..3164 | off | 97 | 1.018 mm | 0.0529 mm | 0.19 mm | **89** of 97 | 3.6 counts |
| 11-25 17:00 | 100 | 1615 / 1585..3152 | 10 × 62 | 99 | 1.051 mm | **0.0101 mm** | 0.04 mm | 0 | 2.99 |
| 11-25 17:33 | 271 (of 500) | 1615 / 1585..3152 | 10 × 62 | 269 | 1.065 mm | **0.0083 mm** | 0.04 mm | 0 | 3.00 ± 0.00 |
| 11-25 18:34 | 1,408 (of 1,500) | 1615 / 1585..3152 | 10 × 62 | 1,406 | 1.062 mm | **0.0167 mm** | 0.27 mm | 3 | 3.004 ± 0.092 |

At a 50 mm lever, 1 mm = 1.146°. SD 0.0083 mm = 34 arcsec = 0.0095°; SD 0.0167 mm = 69 arcsec = 0.019°. The first run (15:17) is **not usable**: most
cycles were flagged stalled, so its statistics mix two populations.

## What the long run (1,408 cycles, 5 h 13 min) shows

* **Typical spread is tiny:** robust SD (1.4826 × MAD) 0.0148 mm, i.e. one or two gauge steps. 90 % of readings are 1.05-1.08 mm.
* **Constant encoder offset:** the servo stops **+3 counts (0.26°)** from the target on 1,403 of 1,406 cycles (the other three stalled at +5). The 3 counts
  equal the arrival tolerance, so it is also what the bench treats as "arrived".
* **Gauge offset vs encoder:** the gauge shows 1.06 mm = 1.2° away from where the gauge was zeroed, about 4.6 times the 0.26° the encoder reports. The gauge zero
  is arbitrary, so this is **not** a backlash number (see 05, OBS-005).
* **Drift:** −0.018 mm per 1,000 cycles (1.071 mm in the first fifth, 1.050 mm in the last fifth). Temperature stayed at about 37 °C and the supply at 12.0 V, so neither explains it.
* **Three stalls** (cycles 729, 1158, 1333): the servo stopped at +5 counts and the gauge read 0.82-0.87 mm (0.2 mm lower).
* **A disturbance near cycles 940-1010:** about 18 readings stuck at exactly 1.00 mm and the rolling SD rose from about 8 µm to 28 µm, then returned to normal. Cause unknown.
* **Hold current:** 4.4 mA mean at the sample moment (0-13 mA).  **Move time:** 1,134 ms mean (1,091-1,478), mostly travel at 1000 counts/s over 1,537 counts.
* **Data-quality notes:** the temperature log contains single spikes (150 °C, 76 °C) while the median is 36-37 °C (sensor glitches; the bench now needs a confirmed reading before it stops);
  the vision model sometimes drops the decimal point (fixed by the digit-count setting).

Evidence: `data/T4.1a/2026-11-25_183449_T4.1a_feetech-factory-3.10_legacy/` and its report in `docs/test_reports/repeatability/`.

## Not measured yet on F

T4.2b (up/down), T3.1 (dead zone / hysteresis / jitter), T3 (backlash), hold tests. The earlier Hold & PID runs on this firmware were trial runs of the new tab and were deleted.
