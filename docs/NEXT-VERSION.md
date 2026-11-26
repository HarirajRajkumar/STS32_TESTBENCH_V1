# Bench versions and notes for the next upgrade

## v1.8.0 (current, 2026-11-26)

* Reports are sorted by kind: `docs/test_reports/hold-pid/` (T5-hold), `repeatability/` (T4.1a / T4.2b up/down runs),
  `repeatability/trials/`, `repeatability/static-reports/` (hand-made merged/daily pages). `storage.report_dir()` decides.
* `docs/test_reports/00_library.html` lists every report (searchable, trials folded away) and is rewritten after every
  run. `storage.write_all_reports()` rebuilds all reports into the sorted folders (used once for the 49 earlier runs).
* Raw data stays in `data/<test_id>/<run>/` (T4.1a, T5-hold): the library links to each run's data folder.

## v1.7.0 (2026-11-26)

* New **Hold & PID** tab and `hold.py`: torque-on hold with optional ± steps, live position / error plots, step-response
  metrics, report `T5-hold` in `data/` and `docs/test_reports/` (interactive). Legacy P / D / I registers are read and
  recorded; the RoboNine kp / kd / ki come from the controller config.
* Restored routes and the safer camera switch from the incident below are part of this version.
* Tested with a simulated servo (step response, jog while holding, report generation) and the page in headless Chrome.
  **Not yet tested on the real servo**: first run should be a pure hold (step 0) with the lever free, then steps of 50.
* Known gaps: the controller config cannot be changed while a hold runs (it needs a new session); the gauge camera is not
  recorded during a hold; the PID registers of the legacy mode are shown but not editable.

## v1.6.0 (2026-11-26)

* Live plot, run detail and comparison are interactive plotly charts. Hovering a reading shows the camera picture of
  that cycle with Qwen's raw text, arrival status, encoder error and temperature. Stalled cycles are red crosses.
* New report `docs/test_reports/<run>_interactive.html`, written by `storage.finalize()` after every run and after every
  correction on the Results tab. It links the frames relatively (`../../data/<test>/<run>/frames/`).
  `storage.write_all_interactive()` rebuilt it for the 28 runs recorded before this version.
* The README index has a new "Interactive" column. The comparison table has a camera column.
* Files: `static/benchplot.js` (shared by the app and inlined into every report), `static/vendor/plotly.min.js`
  (plotly.js 2.35.2, from jsdelivr), `docs/test_reports/assets/plotly.min.js` (same file, used by the reports).
* Tested with headless Chrome (hover shows the right frame in a report, the run detail and the comparison) on
  2026-11-26. Not yet tested during a long live run in the bench window.

## Incident 2026-11-26: motor routes deleted from app.py, then restored

The camera-list patch (v1.6.0) replaced the text from `@app.route("/api/cameras")` up to the "camera + vision"
comment, which also removed the 12 routes between them (connect, select, scan, disconnect, torque, move, calib/center,
calib/sweep, calib/mark, calib/positions, estop, limits). The bench then answered "not found" to Connect.
They were rebuilt from `__pycache__/app.cpython-313.pyc` (Nov 25 16:52); the rebuilt code compiles to the same
bytecode as the original for all 13 functions. Lesson: edit by exact `Edit` replacement of one function, and keep the
folder under git (note 10 below), because Drive kept no earlier revision of `app.py`.

Camera fix in the same version: `Camera.open()` opens and checks the new camera before it stops the old one and the
capture thread now owns its `cv2.VideoCapture`, so switching cameras can no longer release it under a running read
(the "unknown OpenCV error"). A failed switch keeps the working camera.

## Notes for the next version

1. **Camera change inside a series.** Changing the camera or the gauge box stops the run today. Add a "camera
   session" marker: record camera index, rotation, flip and box on every sample (not just in `meta.json`), draw a
   vertical line and a colour change in the plot where it changes, and warn in the comparison when runs used different
   camera settings.
2. **Resume after a camera change.** Let a stopped run continue into a new run that is linked to the first (same
   settings, cycle numbers continue), so one series can be reported as a whole.
3. **Hover pictures in a copied report.** The report only shows pictures while `data/` sits next to `docs/`. Option:
   embed small JPEG thumbnails (about 4 KB each, base64) so one HTML file is self-contained, for emailing.
4. **Qwen misreads.** Click a point in the interactive plot to open the correction (manual value / exclude) instead of
   going through the table. Mark readings whose Qwen raw text had no decimal point or fewer digits than expected.
5. **Telemetry plots.** Add temperature, current and voltage vs time from `telemetry.csv` as interactive charts, with
   the wobble phase shaded. Also the approach trajectory of the selected cycle (hover a reading, see its trajectory).
6. **Arrival tolerance.** Run 2026-11-25_151700 had 89 of 97 cycles stalled by a 1-count miss (error 4, tolerance 3).
   Show a warning in the Run tab when more than 10% of cycles stall in the first 20, and offer to stop.
7. **Temperature spikes.** The log has 150 °C readings while the median is 36 °C. Reject the value (read twice) before
   logging, and exclude it from the `max_temp_c` check and from the report conditions.
8. **Statistics.** Report the sd with stalled cycles excluded and included, and the drift per 1,000 cycles, in the
   Summary and in the text report. The daily report (`2026-11-25_T4.1a_daily-report.html`) does this by hand today.
9. **One copy of plotly.** The bundle (4.5 MB) is stored twice. Either let the reports point to the bench copy or move
   it to one `assets/` folder.
10. **Hold test ideas.** Edit legacy P / D / I from the tab (settings lock, backup first); apply the controller config
    live; frequency sweep or a sine target for the loop bandwidth; record a camera frame every N seconds; compare two
    holds in one plot; load test with a known lever mass.
11. **Version control.** This folder is not a git repository. Put `software/sts3215-repeat-bench` under git and ignore
    `data/`, `frames/` and `__pycache__`.
