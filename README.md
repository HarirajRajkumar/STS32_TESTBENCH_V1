# STS3215 Repeatability Bench

This bench measures the repeatability of one STS3215 servo. It compares Feetech factory firmware 3.10 with RoboNine
STS32XX firmware v9, using a dial gauge read by a camera and Qwen.

**Start:** double-click `Start STS Bench.bat`. The bench opens in its own window. Closing the window stops any run
(its files are finished and saved) and releases COM7 and the camera. To use a browser tab instead, run
`python app.py --browser`. Close FT SCServo Debug first, because only one program can use COM7 at a time.

## Setup before every test series (Bench tab, top card)

1. **Centre.** Point the lever straight up and click **Set centre = 2048**. This writes 128 to register 0x28, so the
   servo saves its current position as 2048.
2. **Travel limits.** Click **Start limit sweep**, which turns torque off. Move the lever by hand back to its stop,
   then forward onto the dial gauge. **Save limits** stores min + margin and max − margin. By default it also writes
   them to the servo (0x09–0x0C), so neither firmware can drive the lever past the stop or into the gauge.
3. **Target.** Turn torque on and jog until the gauge is in the middle of its travel. Then click **Use present
   position as test target**.
4. **Run tab.** Click **TRIAL (3 cycles)** and check the readings and frames. Then click **START FULL RUN** with
   your cycle count, for example 100. A trial is saved as `<run>_trial` and marked as a trial in the reports.

The centre and limit values are stored in each run's `meta.json` and shown in its report.

## The window

The window shows one numbered step at a time: **1 Connect · 2 Camera · 3 Centre · 4 Limits · 5 Target · 6 Run test**.
A step turns green when it is done. Each step has short instructions and its main buttons; less-used settings are
under **More** or **Advanced settings**. The camera and live position, temperature, current and voltage stay on the
right. **Results** compares runs, and **Change firmware** gives the flash commands. **E-STOP** (top right) turns
torque off at once and stops the run.

In step 5, **Standard** control is the legacy STS position registers (the same commands on both firmwares), and
**RoboNine controller** is the v9 extension mode.

**Hold & PID (v1.7.0).** The **Hold & PID** tab keeps the torque on at one position and logs how the position loop holds it
(about 100 samples/s: position, goal, error, current, load, temperature). Push the lever by hand to see how it fights
back, or set *Step ± counts* and the bench steps the goal around the target and measures rise time (10→90 %),
overshoot, settling time (band ±max(2 counts, 2 % of the step)) and steady-state error. The −100…+100 buttons move the
target while holding. *Standard* uses the servo's own P / D / I (registers 0x15-0x17 are read and recorded, never
written); *RoboNine controller* uses kp / kd / ki from the controller config, applied when the hold starts (edit, Save,
Stop, Start). **Stop** keeps the lever held; **E-STOP** cuts torque. Each hold is saved to `data/T5-hold/<run>/` and
`docs/test_reports/<run>_interactive.html`, so a PID change can be compared run to run.

**Interactive plots (v1.6.0).** The live plot, a run's detail view and the comparison are plotly charts. Hover a point to
see the picture the camera took for that cycle (with Qwen's raw text, arrival status, encoder error and temperature);
✕ marks cycles that did not arrive. Drag to zoom, double-click to reset. The live plot keeps your zoom while the run
continues. Each run also writes `docs/test_reports/<run>_interactive.html` with the same hover pictures. Plotly is
bundled (`static/vendor/plotly.min.js`, copied to `docs/test_reports/assets/`), so nothing needs the internet.
The camera settings of each run (index, rotation, flip, gauge box) are in the report and the comparison table, so a
run after a camera change can be told apart.

**Wobble** (step 5, off when wiggles = 0) disturbs each approach before the measured move to the gauge:
*on the way* wiggles ±size counts around the midpoint; *at the gauge* touches the gauge and lifts off by size counts.
The final move and the reading are unchanged, so compare runs with and without wobble to see whether the touch
point moves. Every wobble move is in `telemetry.csv` with phase `wobble`.

**Gauge decimals** (step 2): the LCD decimal point is faint and Qwen drops it (1.00 → "100", 1.01 → "10.1").
With the digit count set (2 for 0.00 mm), the bench uses only Qwen's digits and places the point itself; a reading
with too few digits counts as unread. Older runs: Results → open the run → **Fix decimal point**.

**Change firmware** uploads RoboNine v9 (`releases/custom`, downloaded from the RoboNine GitHub release) or the
bundled Feetech factory 3.10 with RoboNine's own `tools/flash/flash.py`. The bench turns torque off, locks settings
writes, backs up the servo settings to `firmware/.../backups/settings/`, runs the flash, reconnects and shows the
new firmware. After a flash, redo Centre and Positions.

The version number is in the window title and the header, and each run's `meta.json` records it (`app_version`).
Only one copy of the bench can run; a second start says so and exits.

## One cycle

For approach direction *d*, a cycle runs these steps:

1. Move to `target − d·offset`.
2. Move to `target`.
3. Wait for the dwell time.
4. Read the encoder.
5. Take a fresh camera frame and save the crop.
6. Queue a Qwen read in the background, so motion does not wait.

Arrival means the servo is not moving and is within the tolerance for 3 polls. If the servo stops outside the
tolerance, the sample is recorded as `stalled`, not hidden.

* **Legacy mode** uses one write at registers 0x29–0x2F (acceleration, goal position, speed). Both firmwares get
  identical commands. Acceleration is set as counts/s² ÷ 100.
* **Extension mode** (RoboNine only) writes the 0xA0 configuration and reads it back. It then selects session 0xB8,
  sets torque 40=1 and checks feedback. Moves use `TARGETS 0x9A` with the same speed and acceleration. Arrival comes
  from the 0xC1 feedback (accepted sequence, motion flag clear). At the end it turns torque off, returns to legacy mode
  and holds position, except after an E-STOP.

## Fair comparison

Keep these the same in every run: target, offset, speed, acceleration, dwell, cycles, supply voltage, gauge position
and lever. Suggested order:

1. Feetech 3.10 in Legacy mode, T4.1a and T4.2b.
2. Flash RoboNine, then run Legacy mode again.
3. Run Extension mode.
4. Optional: flash the factory firmware back and repeat step 1, to show that the servo itself did not drift.

## Files

The test definitions are in `docs/TEST-PLAN.md`.

| When | Where |
|---|---|
| During a run | `%LOCALAPPDATA%\sts-bench\staging\<run>`, on local disk |
| After the run | `01_JOINTX-SO-101/data/<test>/<run>/`, containing `run.xlsx`, `samples.csv`, `telemetry.csv`, `meta.json`, `summary.json` and `frames/` |
| Report | `01_JOINTX-SO-101/docs/test_reports/<run>.md` |
| Index | `docs/test_reports/README.md` and `data/runs-index.csv` |

`run.xlsx` has five sheets:

* **Summary:** repeatability per direction, plus temperature, current and voltage ranges, and Qwen statistics.
* **Samples:** one row per reading.
* **Telemetry:** every poll, with temperature, current, voltage, load, speed and position.
* **Parameters**
* **Columns:** what each column means.

Corrections made in the table regenerate the CSV, Excel file and report. Close the workbook in Excel first.

A run that was interrupted is moved and marked `interrupted` at the next start.

**Camera:** set **Rotate / Mirror** on the camera card so the digits read upright in the preview. The setting applies
to the crop sent to Qwen and to the saved frames. An inverted 2.36 is read as 6.22. Changing the orientation clears
the gauge box, so draw it again.

## Qwen

Qwen uses the same server, prompt and key as the MOTORON bench:

* Server: `MOTORON_VISION_URL`, default `http://172.16.0.30:4000/v1`
* Model: `vision`
* Key: `MOTORON_VISION_KEY`
* Prompt: copied verbatim into `prompts/`

Thinking mode is off. The value comes from the characters the model read (`raw_text`), not from its own `value`
field. Check the frames for the known 8↔0 misread before you trust a run.

## Check

`python selfcheck.py` runs offline, with no port, camera or Qwen. It checks the protocol framing against the
RoboNine documentation example, Qwen reply parsing and the statistics. It also runs complete T4.1a and T4.2b tests in
both modes against a simulated servo.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): the 2026-11-25 incident (settings area wiped after the
RoboNine flash), how to recognise each failure state, firmware recovery with `flash.py recover`, and
**Change firmware → Restore servo settings** for a wiped settings area.
