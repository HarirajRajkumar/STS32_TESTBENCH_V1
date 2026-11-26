# Observations: Feetech factory firmware vs RoboNine firmware (STS3215)

**Goal:** find out whether RoboNine's STS32XX firmware (v9, which its author says fixes weaknesses of the stock firmware) makes
one STS3215 servo better than Feetech's factory firmware 3.10: less dead zone, less hysteresis, tighter repeatability, less jitter.
Same servo, same jig, same lever, same gauge, same supply, only the firmware and control mode change.

This folder is the observer's notebook. It records what was done, what was seen, and what is still open, with a link to the
evidence (run folder or report) for every claim. Written 2026-11-26 from everything done up to that day.

## Read in this order

| File | What it holds |
|---|---|
| [00-reference-robonine-article.md](00-reference-robonine-article.md) | The RoboNine article we compare against: claims, numbers, methods, and which of our tests answers each claim |
| [01-setup-and-method.md](01-setup-and-method.md) | Hardware, bench software (v1.10.0), conditions F / RL / RE / F2, the tests and how they are run |
| [02-timeline.md](02-timeline.md) | Dated log of everything done, 2026-11-25 and 2026-11-26 |
| [03-results-feetech-factory.md](03-results-feetech-factory.md) | Measured results on the Feetech factory firmware 3.10 |
| [04-results-robonine-v9.md](04-results-robonine-v9.md) | Measured results on RoboNine v9 (legacy and extension modes) |
| [05-comparison-feetech-vs-robonine.md](05-comparison-feetech-vs-robonine.md) | Side-by-side matrix, preliminary findings, what is missing to decide |
| [06-incidents-and-lessons.md](06-incidents-and-lessons.md) | What went wrong (servo settings wiped, sensor glitches, a deleted-routes mistake) and the rules that came out of it |
| [observation-log.md](observation-log.md) | Numbered observations (OBS-nnn) with evidence and status. Append new ones here. |

## Status board (2026-11-26)

| Condition | Meaning | T4.1a repeatability | T3.1 auto (dead zone, hysteresis, jitter) | T3 push test (gauge) | T4.2b up/down |
|---|---|---|---|---|---|
| **F** | Feetech factory 3.10, legacy | **done** (4 runs, 1,408 cycles longest) | not run | not run | not run |
| **RL** | RoboNine v9, legacy commands | 3-cycle trials only | not run | not run | not run |
| **RE** | RoboNine v9, extension (S-curve + PID) | **done** (1,305 cycles) | not run | not run | not run |
| **F2** | Feetech factory again (control) | not run | not run | not run | not run |

## Evidence locations

* Images: `img/` (for example `img/re-run-outlier-hover.png`: hovering a point of the RoboNine extension run shows the saved gauge picture, 2.24 mm at cycle 1104)

* Raw data: `../../data/<test>/<run>/` (`samples.csv`, `telemetry.csv`, `meta.json`, `summary.json`, `frames/`)
* Reports: `../test_reports/00_library.html` (open this first), plus `repeatability/`, `hold-pid/`, `backlash/`, `auto-test/` folders
* Test definitions: `../TEST-PLAN.md`
* Reference article (PDF print): `../../software/sts3215-repeat-bench/docs/robonine_docs/TESTING STS3215.pdf` (kept out of git)
* Bench software and its change history: `../../software/sts3215-repeat-bench/` (GitHub: STS32_TESTBENCH_V1)

## How to add an observation

1. Run or analyse a test. Note the run id(s).
2. Add an `OBS-nnn` entry at the top of [observation-log.md](observation-log.md): what was seen, numbers, evidence link, confidence,
   what it does **not** show.
3. Update the matrix in [05-comparison-feetech-vs-robonine.md](05-comparison-feetech-vs-robonine.md) and the status board above.
4. Run `python tools/sync_observations.py` in the bench folder to copy this folder into the repository, then commit and push.

Rules for writing: numbers with units and n; say which condition (F / RL / RE / F2), lever and load; separate what was measured from
what is guessed; never compare millimetres across different levers (compare degrees).
