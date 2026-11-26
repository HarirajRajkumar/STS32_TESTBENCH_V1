---
name: operator
description: Guide the human through an STS3215 bench test session for the Feetech-vs-RoboNine firmware comparison (preflight, firmware condition F/RL/RE/F2, which tab to use, fixed parameters, what to record, when to stop). Use when the user says they are about to run or are running a test on the bench, asks "what do I do next", or wants to start the Auto test (T3.1), a T4.1a/T4.2b run, a Hold & PID run or the T3 push test.
---

# Operator: run a test session on the STS3215 bench

You do **not** press the bench's buttons: the human does. You tell them exactly what to do, in order, check the state they report (or read it from
`../../data` and the bench header), and stop them if something looks wrong. Keep instructions short and numbered.

## 0. Know the state first

Ask or read: which firmware is on the servo (bench header chip: "Feetech factory 3.10" or "RoboNine v9.0 (fw 3.10)"), the bench version (header, should be the latest in `app.py` `APP_VERSION`),
what has been run (`../../docs/observations/README.md` status board, `../../docs/test_reports/00_library.html`). Pick the next missing cell of the status board. Default order:
**T3.1 Auto test** in RE → RL → F → F2, then T4.2b, then 100-cycle T4.1a in RL and F2, then T3 once with the beam.

## 1. Preflight checklist (every session)

1. 12 V supply on and the **same PSU**; servo is the **only one on the bus**; FT SCServo Debug closed (only one program may use COM7).
2. Start the bench (`Start STS Bench.bat`). If code changed since the window was opened, **close and restart it**.
3. Step 1 **Connect** (COM7, 1 Mbaud, ID 1): the chip shows the firmware; voltage about 12 V, temperature 30-40 °C.
   *0 V / 10 °C / alarm with the supply on* → stop, use the `servo-recovery` skill.
4. Lever mounted and tight; nothing else touching it; beam near vertical if it is on (see CLAUDE.md rule 5).
5. Write the condition in the run note: firmware, mode, lever (50 mm / beam + length), load, anything unusual (camera change, jig tightened).

## 2. Condition selection

| Want | Do |
|---|---|
| F (Feetech factory, legacy) | *Change firmware* → Feetech factory 3.10 → type FLASH. Needs the servo alone on the bus; takes about a minute; the bench backs up the settings first. |
| RL / RE (RoboNine) | *Change firmware* → Download RoboNine firmware (note the SHA-256) → RoboNine → FLASH. Then RL = control **Standard**, RE = control **RoboNine controller**. |
| after any flash | redo **3 Centre** (lever straight up → Set centre = 2048) and **4 Positions** (away end, gauge end, margin 10, overtravel 30, write to servo). Check the centre reads 2048. |

Never flash without the user's explicit "go". Never use FT SCServo Debug's Upgrade tab.

## 3. The tests (use only these IDs)

### T3.1 Auto test (no gauge): the next test to run
1. Tab **Auto test**. All three checklist items must be green (connected, homed = centre 2048, limits leave ±60 counts around home).
2. Choose the control (Standard or RoboNine controller) to match the condition, press **Run auto test**.
3. Do not touch the lever for about 5 minutes (phases A deadband, B hysteresis, C jitter). The progress bar and the set-vs-current plot show it running.
4. When it says Finished: note the report path shown. Do one run per condition; repeat once if the dead zone estimate looks irregular.
5. Hand over to the observer skill: "new run in data/T3.1/…".

### T4.1a / T4.2b (gauge)
Only when the user wants gauge data. Steps: camera open (step 2) and gauge box drawn, *Test read* matches the gauge; step 5 Run test: pattern same-direction (T4.1a) or
bidirectional (T4.2b), **TRIAL (3 cycles)** first and check readings and that all arrivals are ok, then START FULL RUN (100 cycles; 500-1,500 for endurance). Keep target, offset,
speed (1000), accel (5000), dwell (1.5 s), wobble (10 × 62) **identical** across conditions. If more than 10 % of the first 20 cycles stall, stop and fix the set-up.

### Hold & PID
Tab **Hold & PID**: Use present position, step 0 for a pure hold (lever free), or step 50 for step responses; Stop keeps holding. Compare error sd / peak-to-peak and, with steps, overshoot and settling.

### T3 push test
Hidden for now (gauge needed). If the user asks: see `../../docs/TEST-PLAN.md` "T3 in detail" (fixed torque, beam near vertical, gauge nearer the axis, enter the radius).

## 4. Stop rules (tell the user to press Stop or E-STOP)

* Temperature ≥ 65 °C **confirmed for a second or more** (single spikes of 76 / 150 °C are sensor glitches: ignore). Supply below 11 V. Smoke, noise change, lever hitting a stop → **E-STOP**.
* More than 10 % stalled cycles early, or the gauge reading stuck → stop and fix.
* If the bench shows an error you do not recognise, save the text and the run folder name; do not retry in a loop.

## 5. Close the session

1. Note what ran (run ids), what changed in the set-up, and anything odd, in the conversation.
2. Remind: the servo keeps holding after Stop (torque on); press **E-STOP** or *torque off* before touching the lever.
3. Ask the observer to analyse the new runs. If the user wants it pushed, use the `portfolio` skill.
