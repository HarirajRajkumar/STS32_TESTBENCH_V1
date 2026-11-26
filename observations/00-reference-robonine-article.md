# Reference: RoboNine article "Testing of Feetech STS3215 Servomotor: Backlash, Repeatability, and Torque"

Source: robonine.com (Robo9), printed to PDF on 2026-11-26: `software/sts3215-repeat-bench/docs/robonine_docs/TESTING STS3215.pdf`
(38 pages, 42 reader comments, 27,348 views at print time). The PDF is a third-party page and is not committed to git.
This note is our reading of it. The author's comments are by "Nikita Bragin" (RoboNine).

## What they tested (Feetech STS3215 C018, 1:345 gearbox, 12 V, 30 kg·cm stall, 12-bit magnetic encoder, 4096 steps = 0.088°/step)

| Topic | Their method | Their result | Our matching test |
|---|---|---|---|
| Backlash (free play) | Dial indicator on an **86 mm** lever, free hand rotation, a rubber band of about **30 g** for steady loading; the horn held by the servo's own position hold; five reversals averaged per unit (comment, 2025-10-20) | **1.3 mm → 0.0151 rad ≈ 0.87°**, against **≤ 0.5°** in the datasheet. Another unit of the batch: 0.57° (comment, 2026-04-12). | **T3** (push test, needs the gauge). The bench tab exists (v1.9.0) but is hidden for now. |
| Firmware dead zone | Commands smaller than 10 encoder counts produce no motion | **10 counts (0.88°)**: "built-in 10-count deadband". Comment: "You can tighten the deadband in firmware to recover some of it, at the cost of some buzzing at rest." | **T3.1** (Auto test, phase A) |
| Jitter with an extended arm | Visual: the servo struggles to return precisely, visible vibration | Reported, not quantified | **Hold & PID** tab, or Auto test phase C (hold jitter) |
| Repeatability | 10 cm vertical lever, dial indicator, 20-step increments, 3 cycles each direction, 3 iterations, a **spring probe** that takes up the mechanical slack | **±0.3 mm** at the tip, ≈ **0.17°**, about **2 encoder counts** mean absolute deviation | **T4.1a** (same direction, from one side) and **T4.2b** (both sides) |
| Dynamic torque | 10 cm lever, 1, 1.5, 2 kg | 1.5 kg ok, +15 °C in 10 min; 2 kg → overload protection. Position error under a 10 kg·cm load: 22.5 counts (≈ 2°); under 15 kg·cm: 30 counts (≈ 2.6°) | not planned yet (T6 family) |
| Static (stall) torque | 10 cm lever at 45°, about 3.5 kg | ≈ 35 kg·cm, slightly above the datasheet 30; protection cuts torque to about 20 % on prolonged load | not planned yet |
| Temperature, static load | 1 kg at 15 cm (≈ 15 kg·cm) | 48 °C after 10 min, stable for an hour | **T6.1** (Hold & PID log, partly ready) |
| Temperature, oscillation ±90° | continuous motion | 60 °C after 50 min, 71 °C (overheating) after about 110 min. Datasheet overheat 70 °C; no active shutdown seen, manual cut-off needed | T8 endurance (our T4.1a long runs reached 40 °C at 5 h on a small move) |
| No-load speed | RPM at 100 / 50 / 5 % speed | 45.6 RPM (±2 %), 21.9 RPM, 2.3 RPM | not planned |
| Backlash compensation | Two servos with a rope and pre-tension | Play removed by opposing preload | not planned |

Their summary table also lists: "Firmware not user-upgradeable" as a limitation of the stock servo. This is what the RoboNine
STS32XX firmware changes (flashing is done from our bench: *Change firmware*).

## What the reader comments add (useful for our method)

* "Static bench numbers can give a rosier picture than a manipulator under acceleration" (S. Clark, 2026-04-14): our T4.1a is also a
  static-ish measurement; the Hold & PID and the beam tests are the more realistic ones.
* Several readers got worse numbers with a weak power supply (sagging under load): keep **12 V, same PSU**, and watch the logged voltage.
* "The deadband eats into the 0.088° resolution" (comment 2025-10-27, answer above): the dead zone, not the encoder, limits fine positioning.
* Unit-to-unit spread exists, mostly in backlash. We have one servo, so **conclusions are about this unit**.

## Differences between their set-up and ours (do not compare numbers blindly)

| | Their set-up | Our set-up |
|---|---|---|
| Lever | 86 mm for backlash, 100 mm for repeatability | 50 mm (T4 runs); the 20 × 20 beam, stated as 75 cm, is added for T3 / jitter (length to be confirmed) |
| Gauge | Dial indicator read by eye | Digital gauge LCD read by a camera + Qwen model (0.01 mm resolution) |
| Slack | Spring probe takes up play (repeatability) | Gauge plunger spring only |
| Load | Rubber band ≈ 30 g (backlash) | none during T4.1a / T3.1; fixed load to be chosen for T3 |
| Firmware | Stock Feetech | Stock Feetech 3.10 **and** RoboNine v9 on the same servo |

## Claims to confirm or refute with our data

1. Backlash is about 0.87°, above the 0.5° datasheet value. (T3, gauge needed.)
2. The stock firmware has a 10-count dead zone. (T3.1.) *Early hint: our legacy runs stop at a steady +3 counts from the target in 99.8 % of arrivals, and the encoder reads 0.00 ± 0.08 counts in RoboNine extension mode. See OBS-002.*
3. The dead zone can be tightened in firmware (RoboNine) at the cost of buzzing at rest. (T3.1 phase C, Hold jitter, RL / RE.)
4. Repeatability is about 2 counts (0.17°). (T4.1a / T4.2b: our encoder error is 3 counts in legacy, 0 in extension; the gauge sd is 0.008–0.017 mm at 50 mm = 0.01–0.02°. These are different statistics: do not equate them.)
