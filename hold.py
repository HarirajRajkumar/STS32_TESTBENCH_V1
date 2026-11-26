"""Hold & PID test (T5-hold): keep the servo's torque on at one target and log how well the position loop holds it.

Use it to test the PID settings: hold still while you push the lever by hand (disturbance), or let the bench command
steps of +/- N counts around the target and read off rise time, overshoot, settling time and steady-state error.
Works on both control modes:
    legacy      the servo's own position loop (its P / D / I registers 0x15-0x17 are read and recorded, never written)
    extension   RoboNine controller with the kp / kd / ki in the controller config (0xA0), applied when the hold starts

One thread owns the bus while a hold runs (like a test run), so every motion endpoint is blocked meanwhile.
Files: staging (local disk) while running, then data/T5-hold/<run_id>/ (telemetry.csv, meta.json, summary.json) and
docs/test_reports/hold-pid/<run_id>.md + <run_id>_interactive.html (plotly: position/goal, error, current/load).
"""
import csv
import datetime as dt
import html
import json
import os
import queue
import shutil
import statistics
import threading
import time

import storage
from sts import BusError, ExtSession

TEST_ID = "T5-hold"
MAX_DURATION_S = 3600
COLS = ["t_s", "t_iso", "event", "goal", "pos", "err", "speed", "load", "current_ma", "current_raw", "temp", "voltage",
        "source"]
LEGACY_PID_ADDR = 0x15            # P 0x15, D 0x16, I 0x17, minimum start force 0x18


def staging_dir(run_id):
    return os.path.join(os.path.dirname(storage.STAGING), "hold-staging", run_id)


def slug(s):
    import re
    return re.sub(r"[^A-Za-z0-9.]+", "-", str(s)).strip("-").lower()


# ------------------------------------------------------------------ metrics (pure functions, testable without a servo)
def _rows_between(rows, t0, t1):
    return [r for r in rows if t0 <= r["t_s"] < t1]


def step_metrics(rows, t0, t1, g0, g1):
    """Response of one goal change g0 -> g1 at t0, observed until t1."""
    seg = _rows_between(rows, t0, t1)
    amp = g1 - g0
    if len(seg) < 5 or amp == 0:
        return None
    sgn = 1 if amp > 0 else -1
    t = [r["t_s"] - t0 for r in seg]
    y = [r["pos"] for r in seg]

    def first_cross(frac):
        lvl = g0 + frac * amp
        for ti, yi in zip(t, y):
            if (yi - lvl) * sgn >= 0:
                return ti
        return None
    t10, t90 = first_cross(0.1), first_cross(0.9)
    peak = max((yi - g1) * sgn for yi in y)
    band = max(2.0, 0.02 * abs(amp))
    out_of_band = [ti for ti, yi in zip(t, y) if abs(yi - g1) > band]
    settle = (max(out_of_band) if out_of_band else 0.0)
    if out_of_band and max(out_of_band) >= t[-1] - 1e-9:
        settle = None                                   # never settled inside the window
    tail = [yi for ti, yi in zip(t, y) if ti >= 0.75 * t[-1]]
    cur = [abs(r["current_ma"]) for r in seg if r.get("current_ma") is not None]
    return dict(amplitude=amp, rise_10_90_s=(None if t10 is None or t90 is None else round(t90 - t10, 3)),
                overshoot_pct=round(100.0 * max(0.0, peak) / abs(amp), 1), overshoot_counts=round(max(0.0, peak), 2),
                settle_s=(None if settle is None else round(settle, 3)), settle_band_counts=round(band, 2),
                steady_err_counts=round(statistics.fmean(tail) - g1, 2) if tail else None,
                peak_current_ma=round(max(cur), 1) if cur else None)


def summarise(rows, events, init_t=None, settle_skip_s=1.0, init_skip_s=3.0):
    """rows: telemetry rows; events: [(t_s, goal_before, goal_after)] for every goal change after the first move to
    the target (made at init_t, left out of the hold statistics for init_skip_s)."""
    if not rows:
        return {}
    end = rows[-1]["t_s"]
    marks = [(0.0, init_skip_s)] + ([(init_t, init_skip_s)] if init_t is not None else []) +             [(e[0], settle_skip_s) for e in events]
    marks.sort()
    hold_rows = []
    for i, (t, skip) in enumerate(marks):
        t_next = marks[i + 1][0] if i + 1 < len(marks) else end + 1e-6
        hold_rows += _rows_between(rows, t + skip, t_next)
    errs = [r["err"] for r in hold_rows if r.get("err") is not None]
    cur = [abs(r["current_ma"]) for r in rows if r.get("current_ma") is not None]
    temps = [r["temp"] for r in rows if r.get("temp") is not None]
    out = dict(duration_s=round(end, 2), samples=len(rows),
               rate_hz=round(len(rows) / end, 1) if end > 0 else None,
               hold=dict(n=len(errs), err_mean=round(statistics.fmean(errs), 3) if errs else None,
                         err_sd=round(statistics.pstdev(errs), 3) if len(errs) > 1 else None,
                         err_min=min(errs) if errs else None, err_max=max(errs) if errs else None,
                         err_pk_pk=round(max(errs) - min(errs), 2) if errs else None,
                         note="error = position - goal, counts (1 count = 0.088 deg); first %.1f s after each goal "
                              "change left out" % settle_skip_s),
               current_ma=dict(mean=round(statistics.fmean(cur), 1) if cur else None, max=round(max(cur), 1) if cur else None),
               temp_c=dict(start=temps[0] if temps else None, end=temps[-1] if temps else None,
                           max=max(temps) if temps else None))
    steps = []
    for i, (t, g0, g1) in enumerate(events):
        t_next = events[i + 1][0] if i + 1 < len(events) else end
        m = step_metrics(rows, t, t_next, g0, g1)
        if m:
            m["t_s"] = round(t, 2)
            steps.append(m)
    out["steps"] = steps
    if steps:
        def avg(k):
            v = [s[k] for s in steps if s.get(k) is not None]
            return round(statistics.fmean(v), 3) if v else None
        out["steps_mean"] = dict(n=len(steps), rise_10_90_s=avg("rise_10_90_s"), overshoot_pct=avg("overshoot_pct"),
                                 settle_s=avg("settle_s"), steady_err_counts=avg("steady_err_counts"),
                                 settled=sum(1 for s in steps if s.get("settle_s") is not None))
    return out


# ------------------------------------------------------------------ the test
class HoldTest:
    def __init__(self, app, p):
        self.app, self.p = app, p
        self.rows = []
        self.events = []                      # (t_s, goal_before, goal_after)
        self.state = "starting"
        self.phase = ""
        self.message = ""
        self.estopped = False
        self.stop_ev = threading.Event()
        self.cmds = queue.Queue()             # ("goal", counts) from the UI while holding
        self.lock = threading.Lock()
        self.ext = None
        self.goal = int(p["target"])
        self.base = int(p["target"])
        self.pid = {}
        self.final_dir = None
        self.init_t = None
        fw = app.servo_info or {}
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.run_id = "%s_%s_%s_%s" % (stamp, TEST_ID, slug(fw.get("firmware_label", "unknown")), p["mode"])
        self.dir = staging_dir(self.run_id)
        os.makedirs(self.dir, exist_ok=True)
        s = app.settings
        self.meta = dict(run_id=self.run_id, test_id=TEST_ID, started=dt.datetime.now().astimezone().isoformat(),
                         params=p, servo=fw, port=s.get("port"), camera=s.get("camera"),
                         camera_rotate=s.get("cam_rotate"), camera_flip=s.get("cam_flip"), roi=s.get("roi"),
                         ext_config=s.get("ext_config") if p["mode"] == "extension" else None, status="running")
        self._tf = open(os.path.join(self.dir, "telemetry.csv"), "w", newline="", encoding="utf-8")
        self._tw = csv.DictWriter(self._tf, fieldnames=COLS, extrasaction="ignore")
        self._tw.writeheader()
        self._t_flush = time.monotonic()
        self.t0 = time.monotonic()
        self.thread = threading.Thread(target=self._main, name="hold", daemon=True)

    # ---- helpers
    def alive(self):
        return self.thread.is_alive()

    def request_goal(self, counts):
        lo, hi = self.app.settings["soft_min"], self.app.settings["soft_max"]
        if not lo <= counts <= hi:
            raise ValueError("%d is outside soft limits %d..%d" % (counts, lo, hi))
        self.cmds.put(("goal", int(counts)))

    def _read_pid(self):
        a, sid = self.app, self.p["id"]
        if self.p["mode"] == "extension":
            c = a.settings["ext_config"]
            return dict(kind="extension (0xA0)", kp_q8=c.get("kp_q8"), kd_q8=c.get("kd_q8"), ki_q8=c.get("ki_q8"),
                        integral_limit=c.get("integral_limit"), effort_limit=c.get("effort_limit"),
                        velocity_limit=c.get("velocity_limit"), acceleration_limit=c.get("acceleration_limit"))
        try:
            _, d = a.bus.read(sid, LEGACY_PID_ADDR, 4)
            return dict(kind="legacy registers", P=d[0], D=d[1], I=d[2], min_start_force=d[3])
        except BusError as e:
            return dict(kind="legacy registers", error=str(e))

    def _move(self, goal):
        a, p, sid = self.app, self.p, self.p["id"]
        if self.ext:
            self.ext.move(goal, p["speed"])
        else:
            a.bus.legacy_move(sid, goal, p["speed"], p["acc_reg"])

    def _log(self, event, d, src):
        now = time.monotonic()
        pos = d.get("pos")
        row = dict(t_s=round(now - self.t0, 3), t_iso=dt.datetime.now().isoformat(timespec="milliseconds"),
                   event=event, goal=self.goal, pos=round(pos, 2) if pos is not None else None,
                   err=round(pos - self.goal, 2) if pos is not None else None, temp=d.get("temp"),
                   voltage=d.get("voltage"), source=src)
        if src == "legacy":
            row.update(speed=d.get("speed"), load=d.get("load"), current_ma=d.get("current_ma"))
        else:
            row.update(speed=round(d.get("vel", 0), 1), load=d.get("output"), current_raw=d.get("current_raw"))
        with self.lock:
            self.rows.append(row)
        self._tw.writerow(row)
        if now - self._t_flush > 1.0:
            self._tf.flush()
            self._t_flush = now
        return row

    def _poll(self, event="hold"):
        a, sid = self.app, self.p["id"]
        if self.ext:
            fb = a.bus.ext_feedback(sid)
            if fb.get("fault"):
                raise BusError("controller fault 0x%02X" % fb["fault"])
            return self._log(event, fb, "ext")
        return self._log(event, a.bus.telemetry(sid), "legacy")

    def _set_goal(self, goal):
        before = self.goal
        self._move(goal)
        self.goal = goal
        self.events.append((round(time.monotonic() - self.t0, 3), before, goal))
        self.phase = "goal %d" % goal

    # ---- main loop
    def _main(self):
        a, p, sid = self.app, self.p, self.p["id"]
        step, per_side = int(p["step"]), float(p["step_hold_s"])
        duration = float(p["duration_s"])
        try:
            a.runner_owns_bus = True
            self.pid = self._read_pid()
            self.meta["pid"] = self.pid
            if p["mode"] == "extension":
                self.phase = "extension session start"
                cfg = dict(a.settings["ext_config"])
                cfg["acceleration_limit"] = int(p["accel"])
                self.ext = ExtSession(a.bus, sid, cfg)
                self.ext.start()
            else:
                a.bus.legacy_hold_here(sid)
            self.t0 = time.monotonic()
            first = self._poll("start")
            self.goal = int(round(first["pos"]))      # hold where we are, then go to the target
            self.phase = "moving to target %d" % self.base
            self._move(self.base)
            self.goal = self.base
            self.init_t = round(time.monotonic() - self.t0, 3)
            self.state = "running"
            self.phase = "holding %d" % self.base
            over_since = None
            side, next_step = 1, time.monotonic() + max(2.0, per_side)   # let the first move settle before stepping
            while not self.stop_ev.is_set():
                now = time.monotonic()
                if (duration > 0 and now - self.t0 >= duration) or now - self.t0 >= MAX_DURATION_S:
                    self.message = "duration %.0f s reached" % min(duration or MAX_DURATION_S, MAX_DURATION_S)
                    break
                while not self.cmds.empty():
                    kind, v = self.cmds.get()
                    if kind == "goal":
                        self._set_goal(v)
                        self.base = v                     # steps continue around the new centre
                if step > 0 and now >= next_step:
                    g = self.base + side * step
                    lo, hi = a.settings["soft_min"], a.settings["soft_max"]
                    if lo <= g <= hi:
                        self._set_goal(g)
                    side = -side
                    next_step = now + per_side
                r = self._poll()
                # a single high reading is a bad read (the sensor sometimes returns 76 / 150 C); real heating is slow,
                # so stop only when the temperature stays at or above the limit for 1 s
                if r["temp"] is not None and r["temp"] >= p["max_temp_c"]:
                    over_since = over_since or now
                    if now - over_since >= 1.0:
                        self.message = "temperature %s C >= limit %s C for 1 s" % (r["temp"], p["max_temp_c"])
                        break
                else:
                    over_since = None
                time.sleep(0.005)
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
                    self.ext.stop(to_legacy=True)             # torque off + back to legacy mode
                    if not self.estopped:
                        a.bus.legacy_hold_here(sid)           # keep the lever where it is
            except Exception as e:
                self.message += " | session stop: %s" % e
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
            self.phase = "holding position (torque on)" if not self.estopped and self.state != "error" else self.phase

    # ---- results
    def _finish(self):
        with self.lock:
            rows = [dict(r) for r in self.rows]
        summ = summarise(rows, self.events, self.init_t)
        self.meta.update(status=self.state, message=self.message, finished=dt.datetime.now().astimezone().isoformat(),
                         summary=summ, goal_changes=[list(e) for e in self.events])
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
        return dict(run_id=self.run_id, state=self.state, phase=self.phase, message=self.message, n=n,
                    rows=[{k: r[k] for k in ("t_s", "goal", "pos", "err", "current_ma", "load", "temp", "voltage")
                           if k in r} for r in new[::stride]],
                    goal=self.goal, base=self.base, pid=self.pid, events=len(self.events),
                    data_dir=storage.rel(self.final_dir, storage.PROJECT) if self.final_dir else None,
                    report=storage.rel(os.path.join(storage.report_dir(TEST_ID), self.run_id + "_interactive.html"),
                                       storage.PROJECT) if self.final_dir else None)


# ------------------------------------------------------------------ reports
def _f(v, d=2):
    return "-" if v is None else ("%." + str(d) + "f") % v


def write_reports(run_dir, meta, rows, summ):
    """docs/test_reports/<run>.md and <run>_interactive.html (plotly)."""
    rd = storage.report_dir(TEST_ID)
    os.makedirs(rd, exist_ok=True)
    os.makedirs(os.path.join(storage.REPORT_DIR, "assets"), exist_ok=True)
    vend = os.path.join(storage.HERE, "static", "vendor", "plotly.min.js")
    dst = os.path.join(storage.REPORT_DIR, "assets", "plotly.min.js")
    if os.path.exists(vend) and not os.path.exists(dst):
        shutil.copyfile(vend, dst)
    rid, p, sv = meta["run_id"], meta["params"], meta.get("servo") or {}
    pid = meta.get("pid") or {}
    h, c, tt = summ.get("hold") or {}, summ.get("current_ma") or {}, summ.get("temp_c") or {}
    title = "%s · %s · %s" % (TEST_ID, sv.get("firmware_label", "?"), p["mode"])
    pid_txt = ", ".join("%s=%s" % (k, v) for k, v in pid.items() if k != "kind")
    step_rows = "".join(
        "<tr><td>%s</td><td>%+d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            _f(s["t_s"], 1), s["amplitude"], _f(s["rise_10_90_s"], 3), _f(s["overshoot_pct"], 1),
            _f(s["settle_s"], 3) if s["settle_s"] is not None else "not settled", _f(s["steady_err_counts"]),
            _f(s["peak_current_ma"], 0)) for s in summ.get("steps", [])[:200])
    sm = summ.get("steps_mean") or {}
    cam = "camera %s, rotate %s" % (meta.get("camera"), meta.get("camera_rotate"))
    # ---- markdown
    L = ["# %s" % title, "", "| | |", "|---|---|", "| Run | `%s` |" % rid,
         "| Started / finished | %s / %s |" % (meta.get("started"), meta.get("finished")),
         "| Status | **%s** %s |" % (meta.get("status"), meta.get("message") or ""),
         "| Firmware / control | %s / %s |" % (sv.get("firmware_label"), p["mode"]),
         "| PID settings | %s: %s |" % (pid.get("kind"), pid_txt),
         "| Target / step | %s counts / %s counts every %s s |" % (p["target"], p["step"], p["step_hold_s"]),
         "| Speed / accel | %s counts/s / %s counts/s² |" % (p["speed"], p["accel"]), "",
         "## Hold", "",
         "Position error (position − goal, counts) over %s samples at %s Hz: mean %s, sd %s, min %s, max %s, "
         "peak-to-peak %s. Current mean %s mA, max %s mA. Temperature %s → %s °C." % (
             summ.get("samples"), summ.get("rate_hz"), _f(h.get("err_mean"), 3), _f(h.get("err_sd"), 3),
             h.get("err_min"), h.get("err_max"), h.get("err_pk_pk"), c.get("mean"), c.get("max"), tt.get("start"),
             tt.get("end")), ""]
    if summ.get("steps"):
        L += ["## Step response", "",
              "%s steps: rise time (10→90 %%) %s s, overshoot %s %%, settling %s s (band ±max(2 counts, 2 %% of step)), "
              "steady-state error %s counts; %s settled." % (
                  sm.get("n"), sm.get("rise_10_90_s"), sm.get("overshoot_pct"), sm.get("settle_s"),
                  sm.get("steady_err_counts"), sm.get("settled")), ""]
    L += ["## Data", "", "* [telemetry.csv](%s/telemetry.csv) · [meta.json](%s/meta.json) · [summary.json](%s/summary.json)"
          % ((storage.rel(run_dir, rd),) * 3),
          "* Interactive: [%s_interactive.html](%s_interactive.html)" % (rid, rid), ""]
    with open(os.path.join(rd, rid + ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    # ---- html (downsample to <= 6000 points per trace for the browser; the CSV has everything)
    stride = max(1, len(rows) // 6000)
    sub = rows[::stride]
    data = dict(t=[r["t_s"] for r in sub], pos=[r["pos"] for r in sub], goal=[r["goal"] for r in sub],
                err=[r["err"] for r in sub], cur=[r.get("current_ma") for r in sub],
                load=[r.get("load") for r in sub], temp=[r.get("temp") for r in sub])
    with open(os.path.join(storage.HERE, "static", "benchplot.js"), encoding="utf-8") as f:
        js = f.read()
    page = HTML_TEMPLATE
    for k, v in dict(TITLE=html.escape(title), RID=rid, DATA=json.dumps(data, separators=(",", ":")),
                     PIDKIND=html.escape(str(pid.get("kind"))), PID=html.escape(pid_txt),
                     STATUS=html.escape("%s %s" % (meta.get("status"), meta.get("message") or "")),
                     WHEN=html.escape("%s → %s" % (meta.get("started"), meta.get("finished"))),
                     SETUP=html.escape("%s · target %s · step %s counts every %s s · speed %s · accel %s · %s" % (
                         sv.get("firmware_label"), p["target"], p["step"], p["step_hold_s"], p["speed"], p["accel"], cam)),
                     HOLD=html.escape("err mean %s, sd %s, min %s, max %s, peak-to-peak %s counts · current mean %s mA, "
                                      "max %s mA · temp %s → %s °C · %s samples at %s Hz" % (
                                          _f(h.get("err_mean"), 3), _f(h.get("err_sd"), 3), h.get("err_min"),
                                          h.get("err_max"), h.get("err_pk_pk"), c.get("mean"), c.get("max"),
                                          tt.get("start"), tt.get("end"), summ.get("samples"), summ.get("rate_hz"))),
                     STEPMEAN=html.escape("rise time (10→90 %%) %s s · overshoot %s %% · settling %s s · steady error %s counts · %s of %s settled" % (
                         sm.get("rise_10_90_s"), sm.get("overshoot_pct"), sm.get("settle_s"), sm.get("steady_err_counts"),
                         sm.get("settled"), sm.get("n"))) if sm else "no steps (pure hold)",
                     STEPS=step_rows, DIR=storage.rel(run_dir, rd), ASSETS=storage.rel(dst, rd)).items():
        page = page.replace("@@%s@@" % k, str(v))
    page = page.replace("@@JS@@", js)
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
<h1>@@TITLE@@</h1><div class="mut">Hold &amp; PID test. Drag to zoom, double-click to reset. <a href="@@RID@@.md">Text report</a></div>
<div class="card"><table><tr><th>Run</th><td><code>@@RID@@</code></td></tr><tr><th>When</th><td>@@WHEN@@</td></tr><tr><th>Status</th><td>@@STATUS@@</td></tr>
<tr><th>Setup</th><td>@@SETUP@@</td></tr><tr><th>PID settings (@@PIDKIND@@)</th><td>@@PID@@</td></tr></table></div>
<h2>Position and goal</h2><div id="g1" style="height:340px"></div>
<h2>Position error (position − goal, counts)</h2><div id="g2" style="height:280px"></div>
<h2>Current and load</h2><div id="g3" style="height:280px"></div>
<h2>Hold</h2><div class="card">@@HOLD@@</div>
<h2>Step response</h2><div class="card"><p>@@STEPMEAN@@</p><table><tr><th>at s</th><th>step (counts)</th><th>rise 10→90 % (s)</th><th>overshoot %</th><th>settling (s)</th><th>steady error</th><th>peak mA</th></tr>@@STEPS@@</table>
<p class="mut">Settling band: ±max(2 counts, 2 % of the step). Steady error: mean of the last quarter of each window.</p></div>
<p class="mut">Data: <a href="@@DIR@@/telemetry.csv">telemetry.csv</a> · <a href="@@DIR@@/meta.json">meta.json</a> · <a href="@@DIR@@/summary.json">summary.json</a></p>
<script>@@JS@@</script>
<script>
const D=@@DATA@@;
const S=(name,color,y,dash)=>({name,color,mode:'lines',dash,pts:D.t.map((t,i)=>({x:t,y:y[i]}))});
BenchPlot.draw(document.getElementById('g1'),[S('goal','#d97706',D.goal,'dot'),S('position','#2563eb',D.pos)],{dec:1,xtitle:'time (s)',ytitle:'encoder counts',height:340});
BenchPlot.draw(document.getElementById('g2'),[S('error','#dc2626',D.err)],{dec:1,xtitle:'time (s)',ytitle:'error (counts)',height:280});
BenchPlot.draw(document.getElementById('g3'),[S('current (mA)','#16a34a',D.cur),S('load (PWM / output)','#7c3aed',D.load)],{dec:0,xtitle:'time (s)',ytitle:'mA / load',height:280});
</script></body></html>
"""
