# STS3215 repeatability bench: project memory for Claude

**Goal:** compare Feetech's factory firmware 3.10 with RoboNine's STS32XX firmware (v9) on one STS3215 servo, with the same jig, lever, gauge and
supply. RoboNine's article (docs/robonine_docs, kept out of git) claims backlash 0.87° (datasheet ≤ 0.5°) and a 10-count firmware dead zone; its author
says the new firmware tightens the dead zone. We measure it. Everything is evidence-first: every claim in the notes links to a run folder or report.

## Roles (use the skills)

| Role | Skill / agent | Does |
|---|---|---|
| **Operator** | `.claude/skills/operator`, agent `operator` | Guides the human through a test session on the bench (preflight, condition, tab, parameters, what to record, when to stop). Never presses hardware buttons by itself: the human runs the bench. |
| **Observer** | `.claude/skills/observer`, agent `observer` | Reads new runs in `../../data`, computes the statistics, compares with earlier conditions and the RoboNine article, writes numbered observations in `../../docs/observations`, updates the comparison matrix. |
| **Portfolio** | `.claude/skills/portfolio` | Keeps PORTFOLIO.md current and pushes the repo when the user asks. |
| **Recovery** | `.claude/skills/servo-recovery` | The steps when the servo shows 0 V / 10 °C or stops answering. |

## Where things are (relative to this folder)

* Bench code: `app.py` (Flask routes, state), `runner.py` (T4 runs), `hold.py` (Hold & PID), `autotest.py` (T3.1 Auto test), `backlash.py` (T3 push test, tab hidden), `storage.py` (files, reports, library), `sts.py` (bus + RoboNine extension protocol), `static/index.html` (the page), `static/benchplot.js` (plotly hover plot).
* Data: `../../data/<test>/<run>/`. Reports: `../../docs/test_reports/00_library.html` (start here), folders `repeatability/`, `hold-pid/`, `backlash/`, `auto-test/`.
* Test plan: `../../docs/TEST-PLAN.md`. Observer's notebook: `../../docs/observations/` (snapshot copy in `observations/`, refresh with `python tools/sync_observations.py`).
* Start the bench: `Start STS Bench.bat` (window) or `python app.py --browser` (port 5077). **After any code change the open window must be restarted.**
* Offline self-check (no servo, camera or network): `python selfcheck.py` (must print ALL PASSED).
* Git: remote `origin` = GitHub `HarirajRajkumar/STS32_TESTBENCH_V1`, branch `main`. One commit per version, message starts `vX.Y.Z:`.

## Hard rules

1. **Firmware is switched only from the bench** (*Change firmware*). Never use FT SCServo Debug's Upgrade tab (it erased the settings area on 2026-11-25). Only one program may hold COM7.
2. **0 V / 10 °C with the supply on = wiped settings** until proven otherwise. See the recovery skill. Redo *Centre* and *Positions* after every flash.
3. **Use the existing test IDs** (T4.1a, T4.2b, T3, T3.1, T5, T6.1, T7, T8). Do not invent new ones; extend the plan instead.
4. **The gauge is not used for now**: prefer set-vs-current tests (T3.1 Auto test). Gauge-based tests (T4.x, T3) only when the user says so.
5. **Compare degrees, not millimetres**, when the lever differs (50 mm short lever vs the 20 × 20 beam). Record the lever in every run note.
6. **Temperature spikes** (76 / 150 °C single reads) are sensor glitches; do not report them as heating.
7. **Ask before destructive or outward actions**: deleting data, flashing, pushing. Deleting recorded runs needs the user's explicit scope; Drive keeps trash for 30 days.
8. **Edit code with exact one-function replacements**, then run `python selfcheck.py` and re-open the page; never replace a text range that can span several functions (it deleted 12 routes once).
9. Do not commit `data/`, `settings.json`, or the third-party PDF in `docs/robonine_docs/`.
10. No claim without evidence; mark measured vs guessed; one servo means "this unit", not "the firmware in general".

## Current state (update when it changes; last 2026-11-26)

* Bench v1.10.0, pushed. Servo currently flashed with **RoboNine v9** (since 2026-11-26 11:32).
* Done: T4.1a on F (1,408 cycles) and RE (1,305 cycles). Not done: T3.1 in any condition, T4.2b, RL 100 cycles, F2, T3 with the beam.
* Open observations: see `../../docs/observations/observation-log.md` (OBS-001…006).
* Beam: 20 × 20 profile, length stated as 75 cm (to be confirmed); run T3 near vertical, gauge near the axis.
