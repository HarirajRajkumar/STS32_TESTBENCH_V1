"""Auto test (T3.1): automatic, gauge-free test from the homed position, using only the SET value (goal) and the CURRENT
value (encoder). One click, about 5 minutes, no camera, no hand loading.

Starting point: the home position (centre = 2048, set in step 3) and the saved soft limits. Three phases:

  A. Command deadband (T3.1): arrive at home from one side, then command a small REVERSING step of n counts
     (n = 1, 2, 3, 4, 5, 6, 8, 10, 12, 15) and see how far the encoder follows. A servo with a software dead zone ignores
     steps below it. Both sides, 3 repeats. Dead zone estimate = the largest n that the servo still ignores (< 50 % of n).
  B. Hysteresis at the target (T4.2b without the gauge): arrive at a target from below, then from above, 5 repeats, at
     home and at home +/- 150 when the limits allow. Error = current - set. Hysteresis = mean error from below
     minus mean error from above (what the servo itself believes).
  C. Hold jitter: hold home for 10 s and log the error (sd, peak to peak).

The encoder measures the output shaft: play inside the gearbox cannot be seen with it (that needs the gauge, see T3).
What this test shows is the controller: dead zone, hysteresis, steady-state error and jitter, per firmware and mode.
Files: data/T3.1/<run_id>/ and docs/test_reports/auto-test/<run_id>.md + _interactive.html.
"""
import csv
import datetime as dt
import html
import json
import os
import shutil
import statistics
import threading
import time

import storage
from sts import BusError, ExtSession

TEST_ID = "T3.1"
N_LIST = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15]
PRE = 60                      # approach distance (counts) before arriving at a target
TELEM_COLS = ["t_s", "phase", "goal", "pos", "err", "current_ma", "load", "temp", "voltage"]


def staging_dir(run_id):
    return os.path.join(os.path.dirname(storage.STAGING), "auto-staging", run_id)


def slug(s):
    import re
    return re.sub(r"[^A-Za-z0-9.]+", "-", str(s)).strip("-").lower()


def plan_targets(home, lo, hi, amp=150):
    """Targets for phase B: home, home - amp, home + amp, each needing PRE counts of room on both sides."""
    ok = lambda t: lo <= t - PRE and t + PRE <= hi
    if not ok(home):
        return []
    return [t for t in (home - amp, home, home + amp) if ok(t)]


# ------------------------------------------------------------------ results (pure functions)
def deadband_summary(rows):
    """rows: dict(n, side, repeat, response) -> per n table and the dead-zone estimate."""
    table = []
    for n in sorted({r["n"] for r in rows}):
        rr = [r["response"] for r in rows if r["n"] == n]
        med = statistics.median(rr)
        table.append(dict(n=n, median=round(med, 2), mean=round(statistics.fmean(rr), 2), min=round(min(rr), 2),
                          max=round(max(rr), 2), fraction=round(med / n, 2), count=len(rr), follows=med >= 0.5 * n))
    ignored = [t["n"] for t in table if not t["follows"]]
    # dead zone = largest n that is ignored, but only if every smaller n is ignored too (a clean dead zone)
    est = 0
    for t in table:
        if t["follows"]:
            break
        est = t["n"]
    return dict(table=table, deadzone_counts=est, deadzone_deg=round(est * 360 / 4096, 3),
                ignored=ignored, all_followed=not ignored, none_followed=all(not t["follows"] for t in table) if table else False)


def hysteresis_summary(rows):
    out = []
    for t in sorted({r["target"] for r in rows}):
        a = [r["err"] for r in rows if r["target"] == t and r["side"] == "below"]
        b = [r["err"] for r in rows if r["target"] == t and r["side"] == "above"]
        if not a or not b:
            continue
        out.append(dict(target=t, err_below=round(statistics.fmean(a), 2), err_above=round(statistics.fmean(b), 2),
                        sd_below=round(statistics.pstdev(a), 2), sd_above=round(statistics.pstdev(b), 2),
                        hysteresis=round(statistics.fmean(a) - statistics.fmean(b), 2), n=len(a)))
    return dict(targets=out, mean_hysteresis=round(statistics.fmean([o["hysteresis"] for o in out]), 2) if out else None)


def jitter_summary(rows):
    e = [r["err"] for r in rows if r.get("err") is not None]
    if len(e) < 5:
        return {}
    return dict(n=len(e), err_mean=round(statistics.fmean(e), 3), err_sd=round(statistics.pstdev(e), 3),
                err_pk_pk=round(max(e) - min(e), 2), err_min=min(e), err_max=max(e))


# ------------------------------------------------------------------ the test
class AutoTest:
    def __init__(self, app, p):
        self.app, self.p = app, p
        self.rows = []                     # telemetry rows (all phases)
        self.deadband, self.hyst = [], []
        self.state = "starting"
        self.phase = ""
        self.message = ""
        self.progress = 0.0
        self.estopped = False
        self.stop_ev = threading.Event()
        self.lock = threading.Lock()
        self.ext = None
        self.pid = {}
        self.goal = p["home"]
        self.final_dir = None
        fw = app.servo_info or {}
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.run_id = "%s_%s_%s_%s" % (stamp, TEST_ID, slug(fw.get("firmware_label", "unknown")), p["mode"])
        self.dir = staging_dir(self.run_id)
        os.makedirs(self.dir, exist_ok=True)
        s = app.settings
        self.meta = dict(run_id=self.run_id, test_id=TEST_ID, started=dt.datetime.now().astimezone().isoformat(),
                         params=p, servo=fw, port=s.get("port"), calib=s.get("calib"),
                         ext_config=s.get("ext_config") if p["mode"] == "extension" else None, status="running")
        self._tf = open(os.path.join(self.dir, "telemetry.csv"), "w", newline="", encoding="utf-8")
        self._tw = csv.DictWriter(self._tf, fieldnames=TELEM_COLS, extrasaction="ignore")
        self._tw.writeheader()
        self._t_flush = time.monotonic()
        self.t0 = time.monotonic()
        self.thread = threading.Thread(target=self._main, name="autotest", daemon=True)

    def alive(self):
        return self.thread.is_alive()

    # ---- bus
    def _poll(self):
        a, sid = self.app, self.p["id"]
        if self.ext:
            fb = a.bus.ext_feedback(sid)
            if fb.get("fault"):
                raise BusError("controller fault 0x%02X" % fb["fault"])
            d = dict(pos=fb["pos"], current_ma=None, load=fb.get("output"), temp=fb.get("temp"), voltage=fb.get("voltage"))
        else:
            t = a.bus.telemetry(sid)
            d = dict(pos=t["pos"], current_ma=t["current_ma"], load=t["load"], temp=t["temp"], voltage=t["voltage"])
        now = time.monotonic()
        row = dict(t_s=round(now - self.t0, 3), phase=self.phase_tag, goal=self.goal, pos=round(d["pos"], 2),
                   err=round(d["pos"] - self.goal, 2), current_ma=d["current_ma"], load=d["load"], temp=d["temp"],
                   voltage=d["voltage"])
        with self.lock:
            self.rows.append(row)
        self._tw.writerow(row)
        if now - self._t_flush > 1.0:
            self._tf.flush()
            self._t_flush = now
        if d.get("temp") is not None and d["temp"] >= self.p["max_temp_c"]:
            self._over = self._over or now                      # single high reads are sensor glitches: need 1 s
            if now - self._over >= 1.0:
                raise BusError("temperature %s C >= limit %s C for 1 s" % (d["temp"], self.p["max_temp_c"]))
        else:
            self._over = None
        return d

    def _move(self, goal):
        p = self.p
        if self.ext:
            self.ext.move(goal, p["speed"])
        else:
            self.app.bus.legacy_move(p["id"], goal, p["speed"], p["acc_reg"])
        self.goal = goal

    def _wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.stop_ev.is_set():
            self._poll()
            time.sleep(0.01)

    def _arrive(self, goal, timeout=6.0):
        """Move and wait until within 3 counts for 0.4 s (or the timeout)."""
        self._move(goal)
        t0, since = time.monotonic(), None
        while time.monotonic() - t0 < timeout and not self.stop_ev.is_set():
            d = self._poll()
            if abs(d["pos"] - goal) <= 3:
                since = since or time.monotonic()
                if time.monotonic() - since >= 0.4:
                    return
            else:
                since = None
            time.sleep(0.01)

    def _read_current(self, seconds=0.3):
        """Mean encoder position over the last `seconds`."""
        end, vals = time.monotonic() + seconds, []
        while time.monotonic() < end and not self.stop_ev.is_set():
            vals.append(self._poll()["pos"])
            time.sleep(0.01)
        return statistics.fmean(vals) if vals else None

    def _go_side(self, target, side, dwell):
        """Arrive at target from below or above (approach point PRE counts away), dwell, return the current value."""
        self._arrive(target - PRE if side == "below" else target + PRE)
        self._arrive(target)
        self._wait(dwell)
        return self._read_current()

    # ---- main
    def _main(self):
        a, p, sid = self.app, self.p, self.p["id"]
        self._over = None
        self.phase_tag = "start"
        try:
            a.runner_owns_bus = True
            if p["mode"] == "extension":
                self.phase = "extension session start"
                cfg = dict(a.settings["ext_config"])
                cfg["acceleration_limit"] = int(p["accel"])
                self.ext = ExtSession(a.bus, sid, cfg)
                self.ext.start()
                c = a.settings["ext_config"]
                self.pid = dict(kind="extension (0xA0)", kp_q8=c.get("kp_q8"), kd_q8=c.get("kd_q8"), ki_q8=c.get("ki_q8"))
            else:
                a.bus.legacy_hold_here(sid)
                try:
                    _, d = a.bus.read(sid, 0x15, 4)
                    self.pid = dict(kind="legacy registers", P=d[0], D=d[1], I=d[2], min_start_force=d[3])
                except BusError:
                    self.pid = {}
            self.meta["pid"] = self.pid
            self.t0 = time.monotonic()
            home, dwell = p["home"], p["dwell_s"]
            targets = p["targets"]
            total = len(N_LIST) * 2 * p["db_repeats"] + len(targets) * 2 * p["hy_repeats"] + 1
            done = 0
            self.state = "running"
            # ---- A. deadband
            self.phase_tag = "deadband"
            for n in N_LIST:
                for side in ("below", "above"):
                    for rep in range(1, p["db_repeats"] + 1):
                        if self.stop_ev.is_set():
                            raise StopIteration
                        self.phase = "A deadband: step %d counts, from %s, repeat %d" % (n, side, rep)
                        e0 = self._go_side(home, side, dwell)
                        # reversing step: if we arrived from below, step back below (and the other way round)
                        self._move(home - n if side == "below" else home + n)
                        self._wait(0.8)
                        e1 = self._read_current()
                        resp = (e0 - e1) if side == "below" else (e1 - e0)
                        self.deadband.append(dict(n=n, side=side, repeat=rep, e0=round(e0, 2), e1=round(e1, 2),
                                                  response=round(resp, 2)))
                        done += 1
                        self.progress = done / total
            # ---- B. hysteresis at targets
            self.phase_tag = "hysteresis"
            for t in targets:
                for rep in range(1, p["hy_repeats"] + 1):
                    for side in ("below", "above"):
                        if self.stop_ev.is_set():
                            raise StopIteration
                        self.phase = "B hysteresis: target %d from %s, repeat %d" % (t, side, rep)
                        cur = self._go_side(t, side, dwell)
                        self.hyst.append(dict(target=t, side=side, repeat=rep, set=t, current=round(cur, 2), err=round(cur - t, 2)))
                        done += 1
                        self.progress = done / total
            # ---- C. jitter
            self.phase_tag = "jitter_arrive"
            self.phase = "C hold jitter at home for %d s" % p["jitter_s"]
            self._arrive(home)
            self._wait(1.0)
            self.phase_tag = "jitter"
            self._wait(p["jitter_s"])
            self.progress = 1.0
            self.state = "finished"
        except StopIteration:
            self.state = "stopped"
        except BusError as e:
            self.message = "bus error: %s" % e
            self.state = "error"
        except Exception as e:
            self.message = "%s: %s" % (type(e).__name__, e)
            self.state = "error"
        finally:
            self.phase = "finishing"
            try:
                if self.ext:
                    self.ext.stop(to_legacy=True)
                    if not self.estopped:
                        a.bus.legacy_hold_here(sid)
                elif not self.estopped:
                    self._move(p["home"])                      # leave the lever at home, torque on
            except Exception as e:
                self.message += " | stop: %s" % e
            a.runner_owns_bus = False
            try:
                self._tf.close()
            except Exception:
                pass
            try:
                self._finish()
            except Exception as e:
                self.message += " | save failed: %s" % e
                self.state = "error"
            if self.state in ("finished", "stopped"):
                self.phase = "holding home (torque on)"

    # ---- results
    def _summary(self):
        with self.lock:
            rows = [dict(r) for r in self.rows]
        jr = [r for r in rows if r["phase"] == "jitter"]
        j0 = jr[0]["t_s"] if jr else 0.0
        return dict(deadband=deadband_summary(self.deadband), hysteresis=hysteresis_summary(self.hyst),
                    jitter=jitter_summary([r for r in jr if r["t_s"] >= j0 + 0.5]), samples=len(rows)), rows

    def _finish(self):
        summ, rows = self._summary()
        self.meta.update(status=self.state, message=self.message, finished=dt.datetime.now().astimezone().isoformat(),
                         summary=summ, deadband_rows=self.deadband, hysteresis_rows=self.hyst, cycles_done=len(self.deadband) + len(self.hyst))
        dest = storage.final_dir(TEST_ID, self.run_id)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            dest += "_" + dt.datetime.now().strftime("%H%M%S")
        shutil.move(self.dir, dest)
        storage.write_json(os.path.join(dest, "meta.json"), self.meta)
        storage.write_json(os.path.join(dest, "summary.json"), summ)
        self.final_dir = dest
        write_reports(dest, self.meta, rows, summ)
        storage.write_indexes()

    def view(self, since=0, max_rows=400):
        with self.lock:
            n = len(self.rows)
            new = self.rows[since:]
        stride = max(1, len(new) // max_rows)
        return dict(run_id=self.run_id, state=self.state, phase=self.phase, message=self.message, progress=round(self.progress, 3),
                    n=n, rows=[{k: r[k] for k in ("t_s", "goal", "pos", "err")} for r in new[::stride]], pid=self.pid,
                    deadband=deadband_summary(self.deadband) if self.deadband else None,
                    hysteresis=hysteresis_summary(self.hyst) if self.hyst else None,
                    data_dir=storage.rel(self.final_dir, storage.PROJECT) if self.final_dir else None,
                    report=storage.rel(os.path.join(storage.report_dir(TEST_ID), self.run_id + "_interactive.html"),
                                       storage.PROJECT) if self.final_dir else None)


# ------------------------------------------------------------------ reports
def _f(v, d=2):
    return "-" if v is None else ("%." + str(d) + "f") % v


def write_reports(run_dir, meta, rows, summ):
    rd = storage.report_dir(TEST_ID)
    os.makedirs(rd, exist_ok=True)
    os.makedirs(os.path.join(storage.REPORT_DIR, "assets"), exist_ok=True)
    vend = os.path.join(storage.HERE, "static", "vendor", "plotly.min.js")
    dst = os.path.join(storage.REPORT_DIR, "assets", "plotly.min.js")
    if os.path.exists(vend) and not os.path.exists(dst):
        shutil.copyfile(vend, dst)
    rid, p, sv = meta["run_id"], meta["params"], meta.get("servo") or {}
    pid = meta.get("pid") or {}
    pid_txt = ", ".join("%s=%s" % (k, v) for k, v in pid.items() if k != "kind")
    db, hy, jt = summ.get("deadband") or {}, summ.get("hysteresis") or {}, summ.get("jitter") or {}
    title = "%s Auto test · %s · %s" % (TEST_ID, sv.get("firmware_label", "?"), p["mode"])
    if db.get("table"):
        dz = ("The servo ignores command steps up to %d counts (%.2f°): dead zone." % (db["deadzone_counts"], db["deadzone_deg"])
              if db["deadzone_counts"] else ("It followed every reversing step from 1 count up: no dead zone seen."
                                            if db.get("all_followed") else "The response was irregular (see the table)."))
    else:
        dz = "No deadband data."
    hs = ("Mean hysteresis (error from below − error from above) %s counts." % _f(hy.get("mean_hysteresis"), 2)
          if hy.get("targets") else "No hysteresis data.")
    js = ("Hold jitter at home: error sd %s, peak-to-peak %s counts (n %s)." % (_f(jt.get("err_sd"), 3), _f(jt.get("err_pk_pk"), 1), jt.get("n"))
          if jt else "No jitter data.")
    # markdown
    L = ["# %s" % title, "", "| | |", "|---|---|", "| Run | `%s` |" % rid,
         "| Started / finished | %s / %s |" % (meta.get("started"), meta.get("finished")),
         "| Status | **%s** %s |" % (meta.get("status"), meta.get("message") or ""),
         "| Firmware / control | %s / %s |" % (sv.get("firmware_label"), p["mode"]),
         "| PID | %s: %s |" % (pid.get("kind"), pid_txt),
         "| Home / limits | %s / %s..%s counts |" % (p["home"], p["lo"], p["hi"]),
         "| Targets | %s |" % p["targets"], "",
         "Only the set value and the current (encoder) value are used. The gauge is not used.", "",
         "## Result", "", "* " + dz, "* " + hs, "* " + js, "", "## Deadband", "",
         "| Step n (counts) | median response | mean | min | max | response / n | follows |", "|---|---|---|---|---|---|---|"]
    for t in db.get("table") or []:
        L.append("| %d | %s | %s | %s | %s | %s | %s |" % (t["n"], t["median"], t["mean"], t["min"], t["max"], t["fraction"],
                                                          "yes" if t["follows"] else "no"))
    L += ["", "## Hysteresis at targets", "", "| Target | error from below | error from above | hysteresis | n |", "|---|---|---|---|---|"]
    for t in hy.get("targets") or []:
        L.append("| %d | %s ± %s | %s ± %s | %s | %d |" % (t["target"], t["err_below"], t["sd_below"], t["err_above"], t["sd_above"],
                                                           t["hysteresis"], t["n"]))
    rel_data = storage.rel(run_dir, rd)
    L += ["", "## Data", "", "* [telemetry.csv](%s/telemetry.csv) · [meta.json](%s/meta.json) · [summary.json](%s/summary.json)" % ((rel_data,) * 3),
          "* Interactive: [%s_interactive.html](%s_interactive.html)" % (rid, rid), ""]
    with open(os.path.join(rd, rid + ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    # html
    stride = max(1, len(rows) // 6000)
    sub = rows[::stride]
    jrows = [r for r in rows if r["phase"] == "jitter"]
    j0 = jrows[0]["t_s"] if jrows else 0
    data = dict(db=meta.get("deadband_rows") or [], hy=meta.get("hysteresis_rows") or [], dbs=db.get("table") or [],
                t=[r["t_s"] for r in sub], goal=[r["goal"] for r in sub], pos=[r["pos"] for r in sub],
                jt=[round(r["t_s"] - j0, 3) for r in jrows[::max(1, len(jrows) // 3000)]],
                je=[r["err"] for r in jrows[::max(1, len(jrows) // 3000)]])
    with open(os.path.join(storage.HERE, "static", "benchplot.js"), encoding="utf-8") as f:
        js_src = f.read()
    db_rows = "".join("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
        t["n"], t["median"], t["mean"], t["min"], t["max"], t["fraction"], "yes" if t["follows"] else "<b>no</b>")
        for t in db.get("table") or [])
    hy_rows = "".join("<tr><td>%d</td><td>%s ± %s</td><td>%s ± %s</td><td><b>%s</b></td><td>%d</td></tr>" % (
        t["target"], t["err_below"], t["sd_below"], t["err_above"], t["sd_above"], t["hysteresis"], t["n"])
        for t in hy.get("targets") or [])
    page = HTML_TEMPLATE
    for k, v in dict(TITLE=html.escape(title), RID=rid, ASSETS=storage.rel(dst, rd), DZ=html.escape(dz), HS=html.escape(hs), JS_=html.escape(js),
                     WHEN=html.escape("%s → %s" % (meta.get("started"), meta.get("finished"))),
                     STATUS=html.escape("%s %s" % (meta.get("status"), meta.get("message") or "")),
                     SETUP=html.escape("%s · %s · home %s · limits %s..%s · targets %s · speed %s · accel %s" % (
                         sv.get("firmware_label"), p["mode"], p["home"], p["lo"], p["hi"], p["targets"], p["speed"], p["accel"])),
                     PID=html.escape("%s: %s" % (pid.get("kind"), pid_txt)), DBROWS=db_rows, HYROWS=hy_rows, DIR=rel_data,
                     DATA=json.dumps(data, separators=(",", ":"))).items():
        page = page.replace("@@%s@@" % k, str(v))
    page = page.replace("@@JS@@", js_src)
    with open(os.path.join(rd, rid + "_interactive.html"), "w", encoding="utf-8") as f:
        f.write(page)


HTML_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@</title>
<style>:root{--bg:#fff;--fg:#1d2330;--mut:#5b6475;--card:#f4f6fa;--line:#d9dee8}
@media(prefers-color-scheme:dark){:root{--bg:#14171d;--fg:#e6e9ef;--mut:#9aa3b5;--card:#1d222b;--line:#323946}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif;max-width:1150px;margin:0 auto;padding:20px 16px}
h1{font-size:1.3rem;margin:.2em 0}h2{font-size:1.05rem;margin-top:1.6em}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:10px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;font-size:.9rem}.mut{color:var(--mut)}</style>
<script src="@@ASSETS@@"></script></head><body>
<h1>@@TITLE@@</h1><div class="mut">Automatic test from the homed position using only the set value and the current (encoder) value; the gauge is not used. Drag to zoom, double-click to reset. <a href="@@RID@@.md">Text report</a></div>
<div class="card"><table><tr><th>Run</th><td><code>@@RID@@</code></td></tr><tr><th>When</th><td>@@WHEN@@</td></tr><tr><th>Status</th><td>@@STATUS@@</td></tr>
<tr><th>Setup</th><td>@@SETUP@@</td></tr><tr><th>PID</th><td>@@PID@@</td></tr></table></div>
<h2>Result</h2><div class="card"><ul><li>@@DZ@@</li><li>@@HS@@</li><li>@@JS_@@</li></ul></div>
<h2>A · Command deadband: how far the encoder follows a small reversing step</h2><div id="g1" style="height:340px"></div>
<div class="card"><table><tr><th>step n (counts)</th><th>median response</th><th>mean</th><th>min</th><th>max</th><th>response / n</th><th>follows (≥ 50 %)</th></tr>@@DBROWS@@</table></div>
<h2>B · Error (current − set) after arriving from below and from above</h2><div id="g2" style="height:300px"></div>
<div class="card"><table><tr><th>target</th><th>error from below</th><th>error from above</th><th>hysteresis (below − above)</th><th>n</th></tr>@@HYROWS@@</table></div>
<h2>C · Hold jitter at home (error vs time)</h2><div id="g3" style="height:260px"></div>
<h2>Whole test: set value and current value</h2><div id="g4" style="height:300px"></div>
<p class="mut">Data: <a href="@@DIR@@/telemetry.csv">telemetry.csv</a> · <a href="@@DIR@@/meta.json">meta.json</a> · <a href="@@DIR@@/summary.json">summary.json</a></p>
<script>@@JS@@</script>
<script>
const D=@@DATA@@;
const dbPts=side=>D.db.filter(r=>r.side===side).map(r=>({x:r.n,y:r.response,cap:`step ${r.n} counts from ${side} · repeat ${r.repeat} · encoder moved ${r.response} counts`}));
BenchPlot.draw(document.getElementById('g1'),[
  {name:'ideal (follows the command)',color:'#9ca3af',mode:'lines',dash:'dot',pts:[0,15].map(x=>({x,y:x}))},
  {name:'median',color:'#16a34a',mode:'lines',pts:D.dbs.map(t=>({x:t.n,y:t.median}))},
  {name:'arrived from below',color:'#2563eb',pts:dbPts('below')},{name:'arrived from above',color:'#d97706',pts:dbPts('above')}],
  {dec:1,xtitle:'commanded reversing step (counts)',ytitle:'encoder response (counts)',height:340});
const hyPts=side=>D.hy.filter(r=>r.side===side).map(r=>({x:r.target,y:r.err,cap:`target ${r.target} from ${side} · repeat ${r.repeat} · set ${r.set} current ${r.current} · error ${r.err}`}));
BenchPlot.draw(document.getElementById('g2'),[{name:'from below',color:'#2563eb',pts:hyPts('below')},{name:'from above',color:'#d97706',pts:hyPts('above')}],
  {dec:2,xtitle:'target (counts)',ytitle:'current − set (counts)',height:300});
BenchPlot.draw(document.getElementById('g3'),[{name:'error',color:'#dc2626',mode:'lines',pts:D.jt.map((t,i)=>({x:t,y:D.je[i]}))}],{dec:1,xtitle:'time (s)',ytitle:'error (counts)',height:260});
BenchPlot.draw(document.getElementById('g4'),[{name:'set (goal)',color:'#d97706',mode:'lines',dash:'dot',pts:D.t.map((t,i)=>({x:t,y:D.goal[i]}))},
  {name:'current (encoder)',color:'#2563eb',mode:'lines',pts:D.t.map((t,i)=>({x:t,y:D.pos[i]}))}],{dec:1,xtitle:'time (s)',ytitle:'counts',height:300});
</script></body></html>
"""
