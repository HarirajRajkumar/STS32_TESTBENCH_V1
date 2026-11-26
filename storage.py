"""Where runs live.

    during a run : %LOCALAPPDATA%\\sts-bench\\staging\\<run_id>\\        (local disk: frames + telemetry written fast)
    after a run  : 01_JOINTX-SO-101\\data\\<test_id>\\<run_id>\\          run.xlsx, samples.csv, telemetry.csv, meta.json,
                                                                         summary.json, frames\\
                   01_JOINTX-SO-101\\docs\\test_reports\\<run_id>.md      the report
                   01_JOINTX-SO-101\\docs\\test_reports\\README.md        index of every run (regenerated)
                   01_JOINTX-SO-101\\data\\runs-index.csv                 one row per run (regenerated)
A run left in staging (crash, power cut) is finished as 'interrupted' and moved at the next start.
"""
import csv
import datetime as dt
import glob
import json
import os
import shutil
import statistics

import stats

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.normpath(os.path.join(HERE, "..", ".."))          # 01_JOINTX-SO-101
DATA_ROOT = os.path.join(PROJECT, "data")
REPORT_DIR = os.path.join(PROJECT, "docs", "test_reports")
STAGING = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(HERE, "_local"), "sts-bench", "staging")
OLD_RUNS = os.path.join(HERE, "data", "runs")                         # layout before 2026-11-25 (migrated)

SAMPLE_COLS = ["run_id", "cycle", "dir", "t_iso", "target", "approach_from", "pos", "err_counts", "current_ma", "load",
               "temp", "voltage", "arrive", "arrive_ms", "frame", "qwen_status", "qwen_value", "qwen_raw", "qwen_conf",
               "qwen_ms", "qwen_notes", "manual_value", "exclude", "value_mm"]
TELEM_COLS = ["t_s", "t_iso", "cycle", "dir", "phase", "goal", "pos", "speed", "load", "current_ma", "current_raw",
              "temp", "voltage", "moving", "source"]

COLUMN_DICT = {
    "cycle": "cycle number (1..N)", "dir": "+ = approached from below (target-offset), - = from above",
    "target": "commanded target, encoder counts (4096/rev)", "approach_from": "approach point, counts",
    "pos": "encoder position after dwell, counts (extension mode: Q8 feedback, fractional)",
    "err_counts": "pos - target, counts (1 count = 0.0879 deg)", "current_ma": "present current, mA (6.5 mA/LSB)",
    "load": "legacy: present PWM/load, per-mille; extension: applied output (effort)",
    "current_raw": "extension feedback current-sensor reading vs baseline (not mA)",
    "temp": "servo temperature, deg C", "voltage": "supply voltage, V",
    "arrive": "ok | stalled (stopped outside tolerance) | timeout", "arrive_ms": "time from command to arrival, ms",
    "frame": "gauge crop saved for this sample (frames/)", "qwen_value": "gauge reading parsed from Qwen raw_text, mm",
    "qwen_conf": "Qwen confidence", "qwen_ms": "Qwen latency, ms", "manual_value": "operator correction, mm (wins)",
    "exclude": "operator excluded the sample", "value_mm": "value used in statistics = manual_value or qwen_value",
    "t_s": "seconds since run start", "phase": "approach / target / dwell / sample", "goal": "goal of the current move",
    "moving": "servo moving flag (legacy reg 0x42 / extension motion bit)", "source": "legacy registers or ext 0xC1",
}


# ------------------------------------------------------------------ locations
def staging_dir(run_id):
    return os.path.join(STAGING, run_id)


def final_dir(test_id, run_id):
    return os.path.join(DATA_ROOT, test_id, run_id)


def all_run_dirs():
    out = glob.glob(os.path.join(DATA_ROOT, "*", "*", "meta.json"))
    out += glob.glob(os.path.join(STAGING, "*", "meta.json"))
    return [os.path.dirname(p) for p in out]


def find_run(run_id):
    for d in [staging_dir(run_id)] + glob.glob(os.path.join(DATA_ROOT, "*", run_id)):
        if os.path.exists(os.path.join(d, "meta.json")):
            return d
    return None


def rel(path, start):
    return os.path.relpath(path, start).replace("\\", "/")


# ------------------------------------------------------------------ io
def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def write_samples(run_dir, rows):
    rows = [dict(r, value_mm=stats.final_value(r)) for r in rows]
    tmp = os.path.join(run_dir, "samples.csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SAMPLE_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, os.path.join(run_dir, "samples.csv"))


_NUM = {"cycle": int, "target": float, "approach_from": float, "pos": float, "err_counts": float, "current_ma": float,
        "load": float, "temp": float, "voltage": float, "arrive_ms": float, "qwen_value": float, "qwen_ms": float,
        "manual_value": float, "value_mm": float, "t_s": float, "goal": float, "speed": float, "current_raw": float,
        "moving": float}


def _typed(r):
    for k, t in _NUM.items():
        if k in r:
            try:
                r[k] = t(float(r[k])) if r[k] not in (None, "") else None
            except ValueError:
                r[k] = None
    if "exclude" in r:
        r["exclude"] = str(r["exclude"]).lower() == "true"
    return r


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [_typed(r) for r in csv.DictReader(f)]


def load_run(run_dir):
    return (read_json(os.path.join(run_dir, "meta.json"), {}), read_csv(os.path.join(run_dir, "samples.csv")),
            read_csv(os.path.join(run_dir, "telemetry.csv")))


# ------------------------------------------------------------------ conditions (temperature, current, voltage)
def conditions(rows, telem):
    def rng(vals):
        v = [x for x in vals if x is not None]
        if not v:
            return None
        return dict(min=min(v), max=max(v), mean=statistics.fmean(v), first=v[0], last=v[-1])
    src = telem or rows
    hold = [r for r in rows if not r.get("exclude")]
    return dict(temp_c=rng([r.get("temp") for r in src]), voltage_v=rng([r.get("voltage") for r in src]),
                current_ma_all=(rng([abs(r["current_ma"]) if r.get("current_ma") is not None else None for r in src])
                                or rng([abs(r["current_ma"]) if r.get("current_ma") is not None else None
                                        for r in rows])),
                current_ma_holding=rng([abs(r["current_ma"]) if r.get("current_ma") is not None else None
                                        for r in hold]),
                arrive_ms=rng([r.get("arrive_ms") for r in rows]),
                arrivals_not_ok=sum(1 for r in rows if r.get("arrive") not in ("ok", None)))


def qwen_stats(rows):
    ok = [r for r in rows if r.get("qwen_status") == "ok" and r.get("qwen_value") is not None]
    corrected = [r for r in rows if r.get("manual_value") is not None and r.get("qwen_value") is not None
                 and abs(r["manual_value"] - r["qwen_value"]) > 1e-9]
    ms = [r["qwen_ms"] for r in rows if r.get("qwen_ms")]
    return dict(asked=sum(1 for r in rows if r.get("qwen_status") not in (None, "", "none")), read=len(ok),
                corrected=len(corrected), excluded=sum(1 for r in rows if r.get("exclude")),
                median_ms=statistics.median(ms) if ms else None)


# ------------------------------------------------------------------ Excel
def write_xlsx(run_dir, meta, rows, telem, summ, cond, qs):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(write_only=True)
    bold = Font(bold=True)

    def hdr(ws, cells):
        from openpyxl.cell import WriteOnlyCell
        out = []
        for c in cells:
            cell = WriteOnlyCell(ws, value=c)
            cell.font = bold
            out.append(cell)
        ws.append(out)

    p = meta.get("params", {})
    sv = meta.get("servo") or {}
    ws = wb.create_sheet("Summary")
    for k, v in [("Run", meta.get("run_id")), ("Test", meta.get("test_id")), ("Firmware", sv.get("firmware_label")),
                 ("Control mode", p.get("mode")), ("Started", meta.get("started")), ("Finished", meta.get("finished")),
                 ("Status", meta.get("status")), ("Stop reason / message", meta.get("message")),
                 ("Cycles done / planned", "%s / %s" % (meta.get("cycles_done"), p.get("cycles"))),
                 ("Note", p.get("note"))]:
        ws.append([k, v])
    ws.append([])
    hdr(ws, ["Approach", "n", "mean mm", "SD mm", "range mm", "±3SD mm", "SD arcsec", "range arcsec",
             "encoder err mean (counts)", "encoder err SD (counts)", "unread"])
    for d, g in (summ.get("groups") or {}).items():
        m, a, e = g["gauge_mm"], g.get("gauge_arcsec") or {}, g.get("encoder_err_counts") or {}
        ws.append([d, m.get("n"), m.get("mean"), m.get("sd"), m.get("range"), m.get("rp3s"), a.get("sd"),
                   a.get("range"), e.get("mean"), e.get("sd"), g.get("unread")])
    if "lost_motion_mm" in summ:
        ws.append(["(+) - (-) mean, mm", summ["lost_motion_mm"]])
        ws.append(["(+) - (-) mean, arcsec", summ.get("lost_motion_arcsec")])
    ws.append([])
    hdr(ws, ["Condition", "min", "max", "mean", "first", "last"])
    for k, v in cond.items():
        if isinstance(v, dict):
            ws.append([k, v["min"], v["max"], v["mean"], v["first"], v["last"]])
        elif v is not None:
            ws.append([k, v])
    ws.append([])
    hdr(ws, ["Qwen", "value"])
    for k, v in qs.items():
        ws.append([k, v])

    ws = wb.create_sheet("Samples")
    hdr(ws, SAMPLE_COLS)
    for r in rows:
        r = dict(r, value_mm=stats.final_value(r))
        ws.append([r.get(c) for c in SAMPLE_COLS])

    ws = wb.create_sheet("Telemetry")
    hdr(ws, TELEM_COLS)
    step = max(1, len(telem) // 500000)                   # keep the sheet under Excel's row limit
    for r in telem[::step]:
        ws.append([r.get(c) for c in TELEM_COLS])

    ws = wb.create_sheet("Parameters")
    hdr(ws, ["parameter", "value"])
    for k, v in sorted(p.items()):
        ws.append([k, json.dumps(v) if isinstance(v, (dict, list)) else v])
    for k, v in sorted(sv.items()):
        ws.append(["servo." + k, json.dumps(v) if isinstance(v, (dict, list)) else v])
    for k, v in sorted((meta.get("ext_config") or {}).items()):
        ws.append(["ext_config." + k, v])
    for k in ("port", "camera", "camera_rotate", "camera_flip", "roi", "vision"):
        ws.append([k, json.dumps(meta.get(k)) if isinstance(meta.get(k), (dict, list)) else meta.get(k)])

    ws = wb.create_sheet("Columns")
    hdr(ws, ["column", "meaning"])
    for k, v in COLUMN_DICT.items():
        ws.append([k, v])
    path = os.path.join(run_dir, "run.xlsx")
    try:
        wb.save(path)
        return None
    except PermissionError:
        return "run.xlsx is open in Excel; close it and edit any value to regenerate"


# ------------------------------------------------------------------ report (Markdown, docs/test_reports)
def _f(v, d=3):
    return "–" if v is None else ("%." + str(d) + "f") % v


def write_report(run_dir, meta, rows, summ, cond, qs):
    os.makedirs(REPORT_DIR, exist_ok=True)
    p, sv = meta.get("params", {}), meta.get("servo") or {}
    rid = meta["run_id"]
    data_rel = rel(run_dir, REPORT_DIR)
    L = ["# %s · %s · %s" % (meta.get("test_id"), sv.get("firmware_label", "?"), p.get("mode")), "",
         "| | |", "|---|---|",
         "| Run | `%s` |" % rid,
         "| Test | %s (%s) |" % (meta.get("test_id"), "bidirectional, alternating" if p.get("pattern") == "bidirectional"
                                 else "same-direction, from %s" % ("below" if p.get("direction") == "+" else "above")),
         "| Firmware | %s (model %s) |" % (sv.get("firmware_label"), sv.get("model")),
         "| Control mode | %s |" % ("Legacy STS position registers" if p.get("mode") == "legacy"
                                    else "RoboNine extension v9 (S-curve + PID)"),
         "| Started / finished | %s / %s |" % (meta.get("started"), meta.get("finished")),
         "| Status | **%s** %s |" % (meta.get("status"), meta.get("message") or ""),
         "| Cycles | %s of %s (first %s excluded from statistics) |" % (meta.get("cycles_done"), p.get("cycles"),
                                                                      p.get("skip_first")),
         "| Target / offset | %s / %s counts |" % (p.get("target"), p.get("offset")),
         "| Speed / accel | %s counts/s / %s counts/s² |" % (p.get("speed"), p.get("accel")),
         "| Dwell / tolerance | %s s / %s counts |" % (p.get("dwell_s"), p.get("tol")),
         "| Lever radius to probe | %s mm |" % p.get("lever_r_mm"),
         "| Reader | %s |" % ("Qwen: " + str(meta.get("vision")) if p.get("reader") == "qwen" else p.get("reader")),
         "| Note | %s |" % (p.get("note") or ""),
         "| Trial | %s |" % ("yes: %s-cycle check before the %s-cycle run" % (p.get("cycles"), p.get("planned_cycles"))
                             if p.get("trial") else "no"),
         "| Calibration | centre %s (offset %s) at %s; travel %s..%s, soft limits %s..%s%s |" % (
             (p.get("calib") or {}).get("center_pos"), (p.get("calib") or {}).get("position_correction"),
             (p.get("calib") or {}).get("center_time"), (p.get("calib") or {}).get("sweep_min"),
             (p.get("calib") or {}).get("sweep_max"), (p.get("calib") or {}).get("soft_min"),
             (p.get("calib") or {}).get("soft_max"),
             ", written to servo" if (p.get("calib") or {}).get("limits_written_to_servo") else ""), "",
         "## Repeatability", "",
         "| Approach | n | mean mm | SD mm | range mm | ±3SD mm | SD arcsec | range arcsec | encoder err mean | encoder SD |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for d, g in (summ.get("groups") or {}).items():
        m, a, e = g["gauge_mm"], g.get("gauge_arcsec") or {}, g.get("encoder_err_counts") or {}
        L.append("| %s | %s | %s | **%s** | %s | %s | %s | %s | %s | %s |" % (
            "from below (+)" if d == "+" else "from above (−)", m.get("n", 0), _f(m.get("mean")), _f(m.get("sd"), 4),
            _f(m.get("range")), _f(m.get("rp3s")), _f(a.get("sd"), 1), _f(a.get("range"), 1), _f(e.get("mean"), 2),
            _f(e.get("sd"), 2)))
    if "lost_motion_mm" in summ:
        L += ["", "Mean difference between approach directions (+) − (−): **%s mm = %s arcsec**; encoder %s counts." % (
            _f(summ["lost_motion_mm"]), _f(summ.get("lost_motion_arcsec"), 1),
            _f(summ.get("lost_motion_encoder_counts"), 2))]
    L += ["", "## Conditions", "", "| | min | max | mean | start | end |", "|---|---|---|---|---|---|"]
    names = dict(temp_c="Temperature °C", voltage_v="Supply V", current_ma_all="Current (abs) mA, whole run",
                 current_ma_holding="Current (abs) mA, holding at sample", arrive_ms="Move time to target, ms")
    for k, label in names.items():
        v = cond.get(k)
        if v:
            L.append("| %s | %s | %s | %s | %s | %s |" % (label, _f(v["min"], 1), _f(v["max"], 1), _f(v["mean"], 1),
                                                         _f(v["first"], 1), _f(v["last"], 1)))
    L += ["", "Arrivals not `ok` (stalled / timeout): %s." % cond.get("arrivals_not_ok", 0),
          "", "## Gauge reading", "",
          "Qwen read %s of %s frames; operator corrected %s, excluded %s; median latency %s s." % (
              qs["read"], qs["asked"], qs["corrected"], qs["excluded"],
              _f(qs["median_ms"] / 1000 if qs["median_ms"] else None, 1)),
          "", "## Data", "",
          "* Workbook (Summary, Samples, Telemetry, Parameters, Columns): [run.xlsx](%s/run.xlsx)" % data_rel,
          "* [samples.csv](%s/samples.csv) · [telemetry.csv](%s/telemetry.csv) · [meta.json](%s/meta.json) · "
          "[frames/](%s/frames/)" % (data_rel, data_rel, data_rel, data_rel),
          "* Interactive report (hover a point to see its picture): [%s_interactive.html](%s_interactive.html)" % (rid, rid), ""]
    with open(os.path.join(REPORT_DIR, rid + ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ------------------------------------------------------------------ interactive report (plotly, picture on hover)
def _camera_text(meta):
    flip = meta.get("camera_flip")
    roi = meta.get("roi")
    return "camera %s, rotate %s°%s, gauge box %s" % (
        meta.get("camera", "?"), meta.get("camera_rotate", 0), (", flip " + flip) if flip and flip != "none" else "",
        ("x%.3f y%.3f w%.3f h%.3f" % (roi["x"], roi["y"], roi["w"], roi["h"])) if roi else "not set")


def write_interactive_report(run_dir, meta, rows, summ, cond, qs):
    """docs/test_reports/<run_id>_interactive.html: gauge reading and encoder error per cycle as plotly charts.
    Hovering a point shows the picture the camera took for that cycle (frames/ of the run, linked relatively, so the
    report works wherever data/ and docs/ stay side by side). Plotly is read from docs/test_reports/assets/."""
    import html as _h
    os.makedirs(os.path.join(REPORT_DIR, "assets"), exist_ok=True)
    vend = os.path.join(HERE, "static", "vendor", "plotly.min.js")
    dst = os.path.join(REPORT_DIR, "assets", "plotly.min.js")
    if os.path.exists(vend) and not os.path.exists(dst):
        shutil.copyfile(vend, dst)
    with open(os.path.join(HERE, "static", "benchplot.js"), encoding="utf-8") as f:
        benchplot = f.read()
    p, sv, rid = meta.get("params", {}), meta.get("servo") or {}, meta["run_id"]
    frames_rel = rel(os.path.join(run_dir, "frames"), REPORT_DIR)
    skip = int(p.get("skip_first") or 0)
    pts = []
    for r in rows:
        v = stats.final_value(r)
        if v is None or r.get("cycle", 0) <= skip:
            continue
        pts.append(dict(c=r["cycle"], d=r.get("dir"), v=v, e=r.get("err_counts"), a=r.get("arrive"), t=r.get("temp"),
                        f=r.get("frame") or "", q=r.get("qwen_raw") or "",
                        m=r.get("manual_value") not in (None, "")))
    title = "%s · %s · %s" % (meta.get("test_id"), sv.get("firmware_label", "?"), p.get("mode"))
    rows_html = ""
    for d, g in (summ.get("groups") or {}).items():
        m, a = g["gauge_mm"], g.get("gauge_arcsec") or {}
        rows_html += "<tr><td>%s</td><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            "from below (+)" if d == "+" else "from above (−)", m.get("n", 0), _f(m.get("mean")), _f(m.get("sd"), 4),
            _f(m.get("range")), _f(m.get("rp3s")), _f(a.get("sd"), 1))
    cond_html = ""
    for k, label in dict(temp_c="Temperature °C", voltage_v="Supply V", current_ma_holding="Holding current mA",
                         arrive_ms="Move time ms").items():
        v = cond.get(k)
        if v:
            cond_html += "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                label, _f(v["min"], 1), _f(v["max"], 1), _f(v["mean"], 1))
    meta_rows = [("Run", "<code>%s</code>" % _h.escape(rid)), ("Firmware", _h.escape(str(sv.get("firmware_label")))),
                 ("Control", "legacy position registers" if p.get("mode") == "legacy" else "RoboNine extension v9"),
                 ("Started / finished", "%s / %s" % (_h.escape(str(meta.get("started"))), _h.escape(str(meta.get("finished"))))),
                 ("Status", "<b>%s</b> %s" % (_h.escape(str(meta.get("status"))), _h.escape(meta.get("message") or ""))),
                 ("Cycles", "%s of %s (first %s excluded)" % (meta.get("cycles_done"), p.get("cycles"), skip)),
                 ("Target / offset / speed", "%s / %s counts · %s counts/s · accel %s" % (
                     p.get("target"), p.get("offset"), p.get("speed"), p.get("accel"))),
                 ("Camera", _h.escape(_camera_text(meta))), ("Lever", "%s mm" % p.get("lever_r_mm")),
                 ("Note", _h.escape(p.get("note") or ""))]
    mt = "".join("<tr><th>%s</th><td>%s</td></tr>" % kv for kv in meta_rows)
    page = INTERACTIVE_TEMPLATE
    for k, v in dict(TITLE=_h.escape(title), RID=rid, MT=mt, ROWS=rows_html, COND=cond_html,
                     STALLED=cond.get("arrivals_not_ok", 0), QREAD=qs["read"], QASKED=qs["asked"],
                     QCORR=qs["corrected"], QEXCL=qs["excluded"], DATA=rel(run_dir, REPORT_DIR),
                     PTS=json.dumps(pts, separators=(",", ":")), FR=json.dumps(frames_rel)).items():
        page = page.replace("@@%s@@" % k, str(v))
    page = page.replace("@@JS@@", benchplot)      # last: the JS may contain anything
    with open(os.path.join(REPORT_DIR, rid + "_interactive.html"), "w", encoding="utf-8") as f:
        f.write(page)


INTERACTIVE_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<style>:root{--bg:#fff;--fg:#1d2330;--mut:#5b6475;--card:#f4f6fa;--line:#d9dee8;--plus:#2563eb;--minus:#d97706}
@media(prefers-color-scheme:dark){:root{--bg:#14171d;--fg:#e6e9ef;--mut:#9aa3b5;--card:#1d222b;--line:#323946}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif;max-width:1150px;margin:0 auto;padding:20px 16px}
h1{font-size:1.3rem;margin:.2em 0}h2{font-size:1.05rem;margin-top:1.6em}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:10px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;font-size:.9rem}.mut{color:var(--mut)}</style>
<script src="assets/plotly.min.js"></script></head><body>
<h1>@@TITLE@@</h1><div class="mut">Interactive report. Hover a point to see the picture the camera took for that cycle. Drag to zoom, double-click to reset.
 <a href="@@RID@@.md">Text report</a></div>
<div class="card"><table>@@MT@@</table></div>
<h2>Gauge reading per cycle</h2><div id="g1" style="height:380px"></div>
<h2>Encoder error per cycle (counts)</h2><div id="g2" style="height:300px"></div>
<h2>Repeatability</h2><div class="card"><table><tr><th>Approach</th><th>n</th><th>mean mm</th><th>SD mm</th><th>range mm</th><th>±3SD mm</th><th>SD arcsec</th></tr>@@ROWS@@</table>
<p class="mut">Arrivals not ok (stalled / timeout, drawn as ✕): @@STALLED@@. Qwen read @@QREAD@@ of @@QASKED@@ frames; operator corrected @@QCORR@@, excluded @@QEXCL@@.</p></div>
<h2>Conditions</h2><div class="card"><table><tr><th></th><th>min</th><th>max</th><th>mean</th></tr>@@COND@@</table></div>
<p class="mut">Data: <a href="@@DATA@@/samples.csv">samples.csv</a> · <a href="@@DATA@@/telemetry.csv">telemetry.csv</a> · <a href="@@DATA@@/meta.json">meta.json</a> · <a href="@@DATA@@/run.xlsx">run.xlsx</a></p>
<script>@@JS@@</script>
<script>
const PTS=@@PTS@@, FR=@@FR@@;
function series(key){const out=[];[['+','from below (+)','#2563eb'],['-','from above (−)','#d97706']].forEach(([d,name,col])=>{
  const rr=PTS.filter(r=>r.d===d);if(!rr.length)return;
  out.push({name,color:col,pts:rr.map(r=>({x:r.c,y:key==='v'?r.v:r.e,bad:r.a!=='ok',img:r.f?FR+'/'+r.f:'',
    cap:`cycle ${r.c} · ${d==='+'?'from below':'from above'} · ${r.v.toFixed(2)} mm`+(r.q?` (Qwen saw ${r.q})`:'')+(r.m?' · corrected':'')+` · arrive ${r.a} · enc err ${r.e} counts`+(r.t!=null?` · ${r.t} °C`:'')}))});});return out;}
BenchPlot.draw(document.getElementById('g1'),series('v'),{dec:2,avg:50,height:380});
BenchPlot.draw(document.getElementById('g2'),series('e'),{dec:0,dtick:1,ytitle:'encoder error (counts)',height:300});
</script></body></html>
"""


def write_all_interactive():
    """Interactive report for every published run (used once to add them to runs recorded before v1.6.0)."""
    done = []
    for d in all_run_dirs():
        if d.startswith(STAGING):
            continue
        meta, rows, _ = load_run(d)
        if not meta or not rows:
            continue
        write_interactive_report(d, meta, rows, meta.get("summary") or {}, meta.get("conditions") or {},
                                 meta.get("qwen") or dict(read=0, asked=0, corrected=0, excluded=0))
        done.append(meta["run_id"])
    return done


def write_indexes():
    """docs/test_reports/README.md and data/runs-index.csv: one row per run, newest first."""
    metas = []
    for d in all_run_dirs():
        if d.startswith(STAGING):
            continue
        m = read_json(os.path.join(d, "meta.json"))
        if m:
            metas.append((d, m))
    metas.sort(key=lambda x: x[1].get("started") or "", reverse=True)
    cols = ["run_id", "test_id", "firmware", "mode", "status", "cycles_done", "dir", "n", "sd_mm", "range_mm",
            "sd_arcsec", "lost_motion_mm", "temp_max_c", "current_hold_mean_ma", "note"]
    table = []
    for d, m in metas:
        s, p = m.get("summary") or {}, m.get("params") or {}
        c = m.get("conditions") or {}
        for dr, g in (s.get("groups") or {"": {"gauge_mm": {}}}).items():
            gm = g.get("gauge_mm") or {}
            table.append(dict(run_id=m["run_id"], test_id=m.get("test_id") + (" trial" if p.get("trial") else ""),
                              firmware=(m.get("servo") or {}).get("firmware_label"), mode=p.get("mode"),
                              status=m.get("status"), cycles_done=m.get("cycles_done"), dir=dr, n=gm.get("n"),
                              sd_mm=gm.get("sd"), range_mm=gm.get("range"),
                              sd_arcsec=(g.get("gauge_arcsec") or {}).get("sd"),
                              lost_motion_mm=s.get("lost_motion_mm"),
                              temp_max_c=(c.get("temp_c") or {}).get("max"),
                              current_hold_mean_ma=(c.get("current_ma_holding") or {}).get("mean"),
                              note=p.get("note")))
    os.makedirs(DATA_ROOT, exist_ok=True)
    with open(os.path.join(DATA_ROOT, "runs-index.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(table)
    os.makedirs(REPORT_DIR, exist_ok=True)
    L = ["# Test reports · STS3215 firmware comparison", "",
         "Generated by `software/sts3215-repeat-bench` after every run (do not edit by hand). "
         "Test definitions: [../TEST-PLAN.md](../TEST-PLAN.md). Data: `data/<test>/<run>/`, index `data/runs-index.csv`.",
         "", "| Report | Interactive | Test | Firmware | Mode | Dir | n | SD mm | range mm | SD arcsec | (+)−(−) mm | max °C | status |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in table:
        L.append("| [%s](%s.md) | [plot](%s_interactive.html) | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["run_id"][:17], r["run_id"], r["run_id"], r["test_id"], r["firmware"], r["mode"], r["dir"], r["n"] or 0,
            _f(r["sd_mm"], 4), _f(r["range_mm"]), _f(r["sd_arcsec"], 1), _f(r["lost_motion_mm"]),
            _f(r["temp_max_c"], 0), r["status"]))
    with open(os.path.join(REPORT_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# ------------------------------------------------------------------ finalize / publish
def finalize(run_dir, write_index=True):
    """Recompute summary + conditions, write samples.csv, summary.json, meta.json, run.xlsx, the report and indexes.
    Returns (summary, warning or None). Safe to call again after an operator correction."""
    meta, rows, telem = load_run(run_dir)
    p = meta.get("params", {})
    summ = stats.summarise(rows, float(p.get("lever_r_mm") or 0), int(p.get("skip_first") or 0))
    cond, qs = conditions(rows, telem), qwen_stats(rows)
    meta.update(summary=summ, conditions=cond, qwen=qs)
    write_samples(run_dir, rows)
    write_json(os.path.join(run_dir, "summary.json"), dict(summary=summ, conditions=cond, qwen=qs))
    warn = write_xlsx(run_dir, meta, rows, telem, summ, cond, qs)
    in_staging = os.path.normcase(run_dir).startswith(os.path.normcase(STAGING))
    if not in_staging:
        meta["data_dir"] = rel(run_dir, PROJECT)
        meta["report"] = rel(os.path.join(REPORT_DIR, meta["run_id"] + ".md"), PROJECT)
        write_report(run_dir, meta, rows, summ, cond, qs)
        write_interactive_report(run_dir, meta, rows, summ, cond, qs)
    write_json(os.path.join(run_dir, "meta.json"), meta)
    if write_index and not in_staging:
        write_indexes()
    return summ, warn


def publish(run_dir):
    """Move a finished run from staging (or the old layout) into data/<test_id>/<run_id>, then finalize there."""
    meta = read_json(os.path.join(run_dir, "meta.json"), {})
    dest = final_dir(meta.get("test_id") or "unknown", meta.get("run_id") or os.path.basename(run_dir))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        dest += "_" + dt.datetime.now().strftime("%H%M%S")
    shutil.move(run_dir, dest)
    finalize(dest)
    return dest


def recover():
    """At startup: runs left in staging (crash) and runs in the old software/.../data/runs folder are moved."""
    moved = []
    for root in (STAGING, OLD_RUNS):
        for d in glob.glob(os.path.join(root, "*")):
            mp = os.path.join(d, "meta.json")
            if not os.path.isfile(mp):
                continue
            m = read_json(mp, {})
            if m.get("status") == "running":
                m.update(status="interrupted", message="bench closed during the run; recovered at next start")
                write_json(mp, m)
            try:
                moved.append(publish(d))
            except Exception as e:
                moved.append("FAILED %s: %s" % (d, e))
    return moved
