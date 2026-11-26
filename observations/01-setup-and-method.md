# Set-up and method

## Hardware

| Item | Detail |
|---|---|
| Servo | one STS3215 (Feetech), ID 1, 12 V supply, UART TTL at 1 Mbaud on COM7 through a CH343 USB adapter |
| Lever | **short lever: 50 mm** from the axis to the gauge contact (all T4.1a runs so far). **Beam:** a 20 × 20 profile bar added on 2026-11-26, stated as 75 cm, same idea as the reference article (length and mass to be confirmed) |
| Gauge | digital dial gauge (LCD, 0.01 mm), read from camera frames by a vision model (Qwen) through a gauge box drawn on the camera picture |
| Camera | index 0, rotated 180° (until 2026-11-26 17:44); index 1 on the last trial run. Camera changes are recorded in each run's `meta.json` |
| Position reference | centre = 2048 set from the bench (writes the servo's offset); soft limits written to the servo |

## Firmware conditions (from the test plan)

| Code | Firmware | Control | How it is selected |
|---|---|---|---|
| **F** | Feetech factory 3.10 (model 9.3) | legacy position registers | factory image, bundled in the bench |
| **RL** | RoboNine STS32XX v9 | legacy registers (identical commands to F) | flash RoboNine, control = Standard |
| **RE** | RoboNine v9 | extension: S-curve, PID, velocity feed-forward (config 0xA0) | flash RoboNine, control = RoboNine controller |
| **F2** | Feetech factory 3.10 again | legacy | flash factory back, shows the servo itself did not drift |

The RoboNine image used on 2026-11-26: `firmware/.../tools/flash/releases/custom/firmware.enc`, 25,744 bytes, SHA-256
`71b406a2166a9dec8c02b9daa3c67648c52f045ef03c9cfb80134a0bfa68e003`, label `local-e44c0e6` (the label inside RoboNine's release.json; see
06-incidents about the earlier misreading of that label). Extension config used (defaults): kp_q8 2560, kd_q8 32, ki_q8 4096,
integral_limit 64, effort_limit 1000, velocity_limit 3000, acceleration_limit 10000 (the bench overrides it with the run's accel),
velocity_filter_q8 128, ramp_time_ms 100, ff_pos_gain_q8 74, ff_neg_gain_q8 80, ff_effort_limit 1000, gravity_slew 1000.

## Tests (IDs follow the MOTORON V1.1 / RoboNine article; nothing new was invented)

| ID | What | Uses the gauge? | Bench tab | Status |
|---|---|---|---|---|
| T4.1a | same-direction repeatability: approach one target from one side N times, read the gauge | yes | Run test | ready, used |
| T4.2b | bidirectional: alternate approach sides; the (+) − (−) difference is the lost motion at the target | yes (encoder also logged) | Run test | ready, not run yet |
| T3 | backlash push test: hold, load the lever with the same torque each way, gauge span ÷ radius = angle | yes | Backlash T3 (hidden) | ready, not run |
| **T3.1** | **Auto test: dead zone (1-15 count reversing steps), hysteresis (home ± 150, from below / above), hold jitter, from set value and current value only** | **no** | **Auto test** | ready (v1.10.0), **next to run** |
| T5 / T6.1 | hold for a long time, log drift / temperature / current | no (gauge later) | Hold & PID | partly ready |
| T7, T8 | accuracy steps; endurance (long T4.1a) | yes | Run test | T8 done as long runs |

### How a T4.1a cycle works

For approach direction d: move to `target − d·offset`, then to `target`, dwell 1.5 s, read the encoder, take a fresh camera frame, crop the gauge
box, queue the vision read. Optional **wobble** (10 moves of ±62 counts on the way) disturbs each approach before the measured move. A move
counts as *arrived* when the servo is still and within **3 counts** for 3 polls; otherwise the sample is marked `stalled` / `timeout`.
Statistics skip cycle 1. Gauge decimals are fixed at 2 (the LCD decimal point is faint; Qwen drops it).

### How the Auto test (T3.1) works

Precondition: centre homed to 2048, soft limits saved with at least 60 counts around home. Phase A: arrive at home from one side, command a
reversing step of n = 1, 2, 3, 4, 5, 6, 8, 10, 12, 15 counts, wait 0.8 s, read how far the encoder moved (3 repeats, both sides); dead zone = the
largest ignored step (< 50 % response) with every smaller one ignored. Phase B: arrive at home and home ± 150 from below and above, 5 repeats,
error = current − set, hysteresis = below − above. Phase C: hold home for 10 s, error sd and peak-to-peak. Duration about 5 minutes.

### Conversions used

* 1 count = 360 / 4096 = 0.0879°.  Angle (rad) = gauge mm ÷ lever radius mm.  At 50 mm: 1 mm = 1.146°; 0.5° = 0.44 mm; 0.87° = 0.76 mm.
* Torque from a hanging mass: τ = m·g·r (30 g at 86 mm = 0.025 N·m; the same torque at 50 mm needs about 52 g).

## Software

Bench `sts3215-repeat-bench` (Python / Flask + a web page in its own window), GitHub `HarirajRajkumar/STS32_TESTBENCH_V1`. Versions up to now:
1.5.1 (gauge x.xx) → 1.6.0 interactive plotly charts and interactive reports (hover = camera picture) → 1.7.0 Hold & PID → 1.7.1 temperature-spike guard →
1.8.0 reports sorted into folders + library page → 1.9.0 T3 push test → 1.10.0 Auto test T3.1. Firmware is switched **only from the bench**
(*Change firmware*), which backs up the servo settings first.
