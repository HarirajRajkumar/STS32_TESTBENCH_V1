---
name: observer
description: Analyse new STS3215 bench runs and write evidence-linked observations for the Feetech-vs-RoboNine firmware comparison. Use after a run finishes, when the user asks what the data shows, wants a comparison between conditions F/RL/RE/F2, or asks to update the observations folder, the comparison matrix or the status board.
---

# Observer: turn runs into observations

Your job is to say what the data shows, how sure we are, and what it does not show. You never invent numbers. Every statement links to a run folder or report.
Notebook: `../../docs/observations/` (README status board, `05-comparison-feetech-vs-robonine.md` matrix, `observation-log.md` OBS-nnn entries).

## Steps

1. **Find new runs.** `python -c "import json,glob;..."` over `../../data/*/*/meta.json` (or `../../data/runs-index.csv`, or `../../docs/test_reports/00_library.html`). Skip runs already described in the log. Note for each:
   test id, firmware label (`meta['servo']['firmware_label']`), mode, started/finished, status, cycles, lever (`params.lever_r_mm`), target, limits, camera, `ext_config`.
2. **Check comparability before comparing.** Two runs are comparable only if firmware/mode differ and the rest is the same: lever, target, limits, speed, accel, dwell, wobble, camera, gauge zero (gauge mean level), supply. List every difference in the entry. Never compare mm across levers (use degrees: angle = mm ÷ radius).
3. **Compute per test.**
   * T4.1a / T4.2b (`samples.csv`, skip cycle 1, drop excluded): n, mean, plain SD, **robust SD = 1.4826 × MAD**, range, count of readings beyond ±0.1 mm from the median, stalls (`arrive != ok`), encoder error mean ± SD (`err_counts`), drift per 1,000 cycles (linear fit) and the five-part means, rolling SD for disturbances, temperature (median; ignore single spikes), move time. For T4.2b also the (+) − (−) difference.
   * T3.1 Auto test (`summary.json`): dead zone (counts and degrees), the response table, hysteresis per target, hold jitter (sd, peak-to-peak).
   * Hold & PID (`summary.json`): error mean / sd / peak-to-peak, step rise / overshoot / settling, current, temperature.
   * T3 (`summary.json`): free play mm and degrees per position and overall, encoder-seen fraction, versus 0.5° (datasheet) and 0.87° (RoboNine article).
4. **Look at the pictures** for any outlier or surprise: the interactive report (`../../docs/test_reports/<folder>/<run>_interactive.html`) shows the gauge frame on hover, or open `frames/` directly. Vision misreads (8↔0, dropped decimal point) are the known error; say whether the digits on the picture agree.
5. **Compare with the references:** earlier conditions (matrix in `05-…`) and the RoboNine article (`00-reference-robonine-article.md`). State agreement, disagreement, and the differences in set-up.
6. **Write the observation.** Add `OBS-nnn` at the top of `observation-log.md` using the same layout: title with date and status (open / supported / confirmed / refuted), **Seen** (numbers with units and n), **Reading** (interpretation, clearly labelled as such, with alternatives), **Evidence** (paths), **Not shown**, **Next**. Number continues from the highest existing.
7. **Update** the matrix in `05-…`, the status board in `README.md`, the relevant results file (`03-…` / `04-…`) and, if an incident happened, `06-…`. Keep the "decision rule" section honest: mixed results stay mixed.
8. **Sync and hand over:** `python tools/sync_observations.py`. Tell the user the headline in two or three lines, the one thing that most needs a test next, and ask whether to commit/push (portfolio skill).

## Rules of evidence

* Separate **measured** from **inferred**. Put inferences under "Reading".
* One servo = one unit: write "on this servo", never "the firmware is better/worse in general".
* A difference smaller than the gauge step (0.01 mm ≈ 0.011° at 50 mm) or the encoder step (0.088°) is not a difference.
* Report negative and null results with the same care.
* Quote run ids and file paths exactly. If a number cannot be reproduced from the files, do not use it.
* If the data contradicts an earlier observation, mark the old entry **refuted** or **revised** and say why; do not delete it.

## Handy snippet (robust stats for a T4 run)

```python
import pandas as pd, numpy as np, glob
d = glob.glob("../../data/T4.1a/<run id>*")[0]
s = pd.read_csv(d + "/samples.csv"); s = s[(s.cycle > 1) & (s.exclude != True)].dropna(subset=["value_mm"])
v = s.value_mm; med = v.median(); mad = (v - med).abs().median()
print(len(v), v.mean(), v.std(), 1.4826 * mad, v.max() - v.min(), ((v - med).abs() > 0.1).sum(),
      (s.arrive != "ok").sum(), s.err_counts.mean(), s.err_counts.std(), np.polyfit(s.cycle, v, 1)[0] * 1000)
```
