"""T3 Backlash (free play) push test, guided.

The servo holds a target with torque ON. The operator loads the lever with the SAME small torque in each direction
(a hanging weight, a spring balance, or a rubber band at a fixed stretch) and presses Record when the dial gauge is
steady. For every record the bench stores: the encoder position (mean of a short window), current and load, a picture of
the dial gauge and the Qwen reading (or a typed value). Per repeat:

    free play (mm)   = | gauge(PLUS) - gauge(MINUS) |          angle = free play / lever radius
    seen by encoder  = encoder(PLUS) - encoder(MINUS) counts   (1 count = 0.0879 deg)

If the gauge sees far more angle than the encoder, the play is after the encoder: the servo cannot correct it.
Order per position and repeat:  REST (no load, optional) -> PLUS -> MINUS.  PLUS = load toward higher encoder counts.
Compared with the 0.5 deg of the datasheet and with 0.87 deg measured by another user (1.3 mm at 86 mm).

Same bus ownership rules as the other tests: one thread owns the port, every motion endpoint is blocked meanwhile.
Files: data/T3/<run_id>/ (samples.csv, telemetry.csv, meta.json, summary.json, frames/) and
docs/test_reports/backlash/<run_id>.md + <run_id>_interactive.html (picture on hover).
"""
import csv
import datetime as dt
import html
import json
import math
import os
import queue
import shutil
import statistics
import threading
import time

import camera as camlib
import stats
import storage
from sts import BusError, ExtSession
from vision import apply_decimals

TEST_ID = "T3"
DEG_PER_COUNT = 360.0 / 4096.0
DATASHEET_DEG = 0.5            # Feetech datasheet limit
REFERENCE_DEG = 0.87           # 1.3 mm at an 86 mm lever (other user's measurement), for comparison only
LABEL_TEXT = {"rest": "NO load (let go of the lever)",
              "plus": "load toward HIGHER encoder counts (PLUS)",
              "minus": "load toward LOWER encoder counts (MINUS)"}
SAMPLE_COLS = ["step", "pos_idx", "target", "repeat", "label", "enc_mean", "enc_sd", "enc_n", "current_ma", "load",
               "temp", "voltage", "t_s", "t_iso", "frame", "qwen_status", "qwen_value", "qwen_raw", "qwen_conf",
               "qwen_ms", "qwen_notes", "manual_value", "exclude", "value_mm"]
TELEM_COLS = ["t_s", "step", "label", "pos", "current_ma", "load", "temp", "voltage"]


def staging_dir(run_id):
    return os.path.join(os.path.dirname(storage.STAGING), "backlash-staging", run_id)


def slug(s):
    import re
    return re.sub(r"[^A-Za-z0-9.]+", "-", str(s)).strip("-").lower()


def build_steps(positions, repeats, with_rest):
    steps = []
    for pi, pos in enumerate(positions):
        for r in range(1, repeats + 1):
            for label in (["rest"] if with_rest else []) + ["plus", "minus"]:
                steps.append(dict(pos_idx=pi, target=int(pos), repeat=r, label=label))
    return steps


# ------------------------------------------------------------------ results (pure functions)
def summarise(rows, lever_r_mm):
    """rows: record rows (value_mm = manual or Qwen reading). Returns per-position and overall free play."""
    by = {}
    for r in rows:
        by.setdefault((r["pos_idx"], r["repeat"]), {})[r["label"]] = r
    reps = []
    for (pi, rep), d in sorted(by.items()):
        if "plus" not in d or "minus" not in d:
            continue
        a, b = d["plus"], d["minus"]
        va, vb = a.get("value_mm"), b.get("value_mm")
        enc = a["enc_mean"] - b["enc_mean"]
        item = dict(pos_idx=pi, repeat=rep, target=a["target"], encoder_counts=round(enc, 2),
                    encoder_deg=round(enc * DEG_PER_COUNT, 3), direction_ok=enc > 0)
        if va is not None and vb is not None and lever_r_mm:
            mm = abs(va - vb)
            item.update(free_play_mm=round(mm, 3), free_play_deg=round(math.degrees(mm / lever_r_mm), 3),
                        value_plus=va, value_minus=vb)
            if "rest" in d and d["rest"].get("value_mm") is not None:
                item["plus_from_rest_mm"] = round(abs(va - d["rest"]["value_mm"]), 3)
                item["minus_from_rest_mm"] = round(abs(vb - d["rest"]["value_mm"]), 3)
        reps.append(item)

    def desc(vals):
        v = [x for x in vals if x is not None]
        if not v:
            return {}
        return dict(n=len(v), mean=round(statistics.fmean(v), 3), sd=round(statistics.stdev(v), 3) if len(v) > 1 else 0.0,
                    min=round(min(v), 3), max=round(max(v), 3))
    out = dict(lever_r_mm=lever_r_mm, datasheet_deg=DATASHEET_DEG, reference_deg=REFERENCE_DEG, repeats=reps, positions={})
    for pi in sorted({r["pos_idx"] for r in reps}):
        rr = [r for r in reps if r["pos_idx"] == pi]
        out["positions"][str(pi)] = dict(target=rr[0]["target"], free_play_mm=desc([r.get("free_play_mm") for r in rr]),
                                         free_play_deg=desc([r.get("free_play_deg") for r in rr]),
                                         encoder_deg=desc([r.get("encoder_deg") for r in rr]))
    out["all"] = dict(free_play_mm=desc([r.get("free_play_mm") for r in reps]),
                      free_play_deg=desc([r.get("free_play_deg") for r in reps]),
                      encoder_deg=desc([r.get("encoder_deg") for r in reps]))
    deg = out["all"]["free_play_deg"].get("mean")
    enc = out["all"]["encoder_deg"].get("mean")
    if deg is not None:
        out["verdict"] = dict(within_datasheet=deg <= DATASHEET_DEG, vs_datasheet=round(deg / DATASHEET_DEG, 2),
                              vs_reference=round(deg / REFERENCE_DEG, 2),
                              encoder_sees_fraction=round(enc / deg, 2) if enc is not None and deg else None)
    out["wrong_direction"] = [(r["pos_idx"], r["repeat"]) for r in reps if not r["direction_ok"]]
    return out


# ------------------------------------------------------------------ the test
class BacklashTest:
    def __init__(self, app, p):
        self.app, self.p = app, p
        self.steps = build_steps(p["positions"], p["repeats"], p["with_rest"])
        self.k = 0                                    # index of the step waiting for Record
        self.rows = []
        self.records = []                             # (t_s, step, label, enc_mean) for the plots
        self.state = "starting"
        self.phase = ""
        self.message = ""
        self.warn = ""
        self.estopped = False
        self.stop_ev = threading.Event()
        self.cmds = queue.Queue()
        self.lock = threading.Lock()
        self.q = queue.Queue()                        # Qwen reads in the background
        self.ext = None
        self.pid = {}
        self.cur_goal = None
        self.last_tel = None
        self.final_dir = None
        fw = app.servo_info or {}
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.run_id = "%s_%s_%s_%s" % (stamp, TEST_ID, slug(fw.get("firmware_label", "unknown")), p["mode"])
        self.dir = staging_dir(self.run_id)
        os.makedirs(os.path.join(self.dir, "frames"), exist_ok=True)
        s = app.settings
        self.meta = dict(run_id=self.run_id, test_id=TEST_ID, started=dt.datetime.now().astimezone().isoformat(),
                         params=p, servo=fw, port=s.get("port"), camera=s.get("camera"),
                         camera_rotate=s.get("cam_rotate"), camera_flip=s.get("cam_flip"), roi=s.get("roi"),
                         vision=app.reader.describe() if app.reader else None,
                         ext_config=s.get("ext_config") if p["mode"] == "extension" else None, status="running")
        self._tf = open(os.path.join(self.dir, "telemetry.csv"), "w", newline="", encoding="utf-8")
        self._tw = csv.DictWriter(self._tf, fieldnames=TELEM_COLS, extrasaction="ignore")
        self._tw.writeheader()
        self._t_flush = time.monotonic()
        self.t0 = time.monotonic()
        self.thread = threading.Thread(target=self._main, name="backlash", daemon=True)
        self.reader_thread = threading.Thread(target=self._reader_loop, name="qwen-bl", daemon=True)

    # ---- helpers
    def alive(self):
        return self.thread.is_alive()

    def start(self):
        self.reader_thread.start()
        self.thread.start()

    def command(self, kind, value=None):
        self.cmds.put((kind, value))

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
        step = self.steps[self.k] if self.k < len(self.steps) else None
        row = dict(t_s=round(now - self.t0, 3), step=self.k, label=step["label"] if step else "", **d)
        self._tw.writerow(row)
        self.last_tel = row
        if now - self._t_flush > 1.0:
            self._tf.flush()
            self._t_flush = now
        return d

    def _move(self, goal):
        p = self.p
        if self.ext:
            self.ext.move(goal, p["speed"])
        else:
            self.app.bus.legacy_move(p["id"], goal, p["speed"], p["acc_reg"])
        self.cur_goal = goal

    def _settle(self, goal, timeout=8.0):
        """Wait until the position is within 3 counts of the goal for 0.5 s, then dwell settle_s."""
        t0, ok_since = time.monotonic(), None
        while time.monotonic() - t0 < timeout and not self.stop_ev.is_set():
            d = self._poll()
            if abs(d["pos"] - goal) <= 3:
                ok_since = ok_since or time.monotonic()
                if time.monotonic() - ok_since >= 0.5:
                    break
            else:
                ok_since = None
            time.sleep(0.02)
        end = time.monotonic() + float(self.p["settle_s"])
        while time.monotonic() < end and not self.stop_ev.is_set():
            self._poll()
            time.sleep(0.02)

    def _record(self, manual):
        """Average the encoder over avg_s, grab a gauge picture, store the row, queue the Qwen read."""
        p = self.p
        st = self.steps[self.k]
        t_end = time.monotonic() + float(p["avg_s"])
        pos, cur, load = [], [], []
        last = None
        while time.monotonic() < t_end and not self.stop_ev.is_set():
            d = self._poll()
            pos.append(d["pos"])
            if d.get("current_ma") is not None:
                cur.append(abs(d["current_ma"]))
            if d.get("load") is not None:
                load.append(d["load"])
            last = d
            time.sleep(0.02)
        if not pos:
            return
        t_settled = time.time()
        jpg, fn = None, None
        cam = self.app.camera
        img, _ = cam.fresh_frame(t_settled) if cam.cap else (None, 0)
        if img is not None:
            jpg = camlib.jpeg_bytes(camlib.crop_roi(img, self.app.settings.get("roi")))
            fn = "s%03d_%s.jpg" % (self.k, st["label"])
            with open(os.path.join(self.dir, "frames", fn), "wb") as f:
                f.write(jpg)
        row = dict(step=self.k, pos_idx=st["pos_idx"], target=st["target"], repeat=st["repeat"], label=st["label"],
                   enc_mean=round(statistics.fmean(pos), 2), enc_sd=round(statistics.pstdev(pos), 3) if len(pos) > 1 else 0.0,
                   enc_n=len(pos), current_ma=round(statistics.fmean(cur), 1) if cur else None,
                   load=round(statistics.fmean(load), 1) if load else None, temp=last["temp"], voltage=last["voltage"],
                   t_s=round(time.monotonic() - self.t0, 3), t_iso=dt.datetime.now().isoformat(timespec="milliseconds"),
                   frame=fn, qwen_status="none", manual_value=manual, exclude=False)
        with self.lock:
            self.rows.append(row)
            idx = len(self.rows) - 1
            self.records.append((row["t_s"], self.k, st["label"], row["enc_mean"]))
        if manual is None and p["reader"] == "qwen" and jpg:
            row["qwen_status"] = "pending"
            self.q.put((idx, jpg))
        # the encoder must move the right way between PLUS and MINUS, or the load was applied the wrong way round
        if st["label"] == "minus" and len(self.rows) >= 2 and self.rows[-2]["label"] == "plus" \
                and self.rows[-2]["enc_mean"] <= row["enc_mean"]:
            self.warn = ("PLUS read %.1f counts and MINUS %.1f: the encoder did not move the expected way. Swap the "
                         "two loads (or check that the load really moved the lever)." % (self.rows[-2]["enc_mean"], row["enc_mean"]))
        else:
            self.warn = ""

    def _reader_loop(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            idx, jpg = item
            dec = self.p.get("gauge_decimals")
            res = apply_decimals(self.app.reader.read(jpg, dec), dec)
            with self.lock:
                self.rows[idx].update(qwen_status=res["status"], qwen_value=res["value"], qwen_raw=res.get("raw_text"),
                                      qwen_conf=res.get("confidence"), qwen_ms=res.get("ms"), qwen_notes=res.get("notes"))

    # ---- main loop
    def _main(self):
        a, p, sid = self.app, self.p, self.p["id"]
        try:
            a.runner_owns_bus = True
            if p["mode"] == "extension":
                self.phase = "extension session start"
                cfg = dict(a.settings["ext_config"])
                cfg["acceleration_limit"] = int(p["accel"])
                self.ext = ExtSession(a.bus, sid, cfg)
                self.ext.start()
            else:
                a.bus.legacy_hold_here(sid)
            self.pid = {}
            try:
                if not self.ext:
                    _, d = a.bus.read(sid, 0x15, 4)
                    self.pid = dict(kind="legacy registers", P=d[0], D=d[1], I=d[2], min_start_force=d[3])
                else:
                    c = a.settings["ext_config"]
                    self.pid = dict(kind="extension (0xA0)", kp_q8=c.get("kp_q8"), kd_q8=c.get("kd_q8"), ki_q8=c.get("ki_q8"))
            except BusError:
                pass
            self.meta["pid"] = self.pid
            self.t0 = time.monotonic()
            over_since = None
            while self.k < len(self.steps) and not self.stop_ev.is_set():
                st = self.steps[self.k]
                if self.cur_goal != st["target"]:
                    self.state = "moving"
                    self.phase = "moving to position %d (%d)" % (st["pos_idx"] + 1, st["target"])
                    self._move(st["target"])
                    self._settle(st["target"])
                self.state = "waiting"
                self.phase = "step %d of %d" % (self.k + 1, len(self.steps))
                done = False
                while not done and not self.stop_ev.is_set():
                    try:
                        kind, val = self.cmds.get(timeout=0.04)
                    except queue.Empty:
                        d = self._poll()
                        # single high temperature reads are sensor glitches: stop only if it stays over the limit for 1 s
                        if d.get("temp") is not None and d["temp"] >= p["max_temp_c"]:
                            over_since = over_since or time.monotonic()
                            if time.monotonic() - over_since >= 1.0:
                                self.message = "temperature %s C >= limit %s C for 1 s" % (d["temp"], p["max_temp_c"])
                                self.stop_ev.set()
                        else:
                            over_since = None
                        continue
                    if kind == "record":
                        self.state = "recording"
                        self._record(val)
                        self.k += 1
                        done = True
                    elif kind == "undo" and self.k > 0:
                        with self.lock:
                            if self.rows:
                                self.rows.pop()
                                self.records.pop()
                        self.k -= 1
                        if self.steps[self.k]["target"] != self.cur_goal:
                            self.cur_goal = None                   # go back to that position first
                        self.warn = ""
                        done = True
            self.state = "stopped" if self.stop_ev.is_set() and self.k < len(self.steps) else "finished"
            if self.state == "finished":
                self.message = "all %d steps recorded" % len(self.steps)
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
            except Exception as e:
                self.message += " | session stop: %s" % e
            a.runner_owns_bus = False
            self.q.put(None)
            self.reader_thread.join(timeout=120)
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
    def _rows_final(self):
        with self.lock:
            rows = [dict(r) for r in self.rows]
        for r in rows:
            r["value_mm"] = stats.final_value(r)
        return rows

    def _finish(self):
        rows = self._rows_final()
        summ = summarise(rows, float(self.p["lever_r_mm"]))
        self.meta.update(status=self.state, message=self.message, finished=dt.datetime.now().astimezone().isoformat(),
                         summary=summ, cycles_done=len(rows))
        with open(os.path.join(self.dir, "samples.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SAMPLE_COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
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

    def view(self):
        with self.lock:
            rows = [dict(r) for r in self.rows]
            recs = list(self.records)
        for r in rows:
            r["value_mm"] = stats.final_value(r)
        st = self.steps[self.k] if self.k < len(self.steps) else None
        return dict(run_id=self.run_id, state=self.state, phase=self.phase, message=self.message, warn=self.warn,
                    k=self.k, total=len(self.steps), step=st, prompt=LABEL_TEXT.get(st["label"]) if st else None,
                    rows=rows, records=recs, pid=self.pid, last_tel=self.last_tel, goal=self.cur_goal,
                    summary=summarise(rows, float(self.p["lever_r_mm"])) if rows else None,
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
    allr, ver = summ.get("all") or {}, summ.get("verdict") or {}
    fp, fd, ed = allr.get("free_play_mm") or {}, allr.get("free_play_deg") or {}, allr.get("encoder_deg") or {}
    title = "%s Backlash · %s · %s" % (TEST_ID, sv.get("firmware_label", "?"), p["mode"])
    verdict = ""
    if ver:
        verdict = ("Free play %s deg (sd %s, n %s): %s the %.2f deg datasheet limit (%sx), %sx the 0.87 deg measured by another "
                   "user. The encoder sees %s of it." % (
                       _f(fd.get("mean"), 3), _f(fd.get("sd"), 3), fd.get("n"),
                       "within" if ver["within_datasheet"] else "ABOVE", DATASHEET_DEG, ver["vs_datasheet"],
                       ver["vs_reference"], ("%d %%" % round(100 * ver["encoder_sees_fraction"])) if ver.get("encoder_sees_fraction") is not None else "-"))
    pos_rows = "".join(
        "<tr><td>%d</td><td>%s</td><td>%s ± %s</td><td>%s ± %s</td><td>%s</td><td>%s</td></tr>" % (
            int(k) + 1, v["target"], _f(v["free_play_mm"].get("mean"), 3), _f(v["free_play_mm"].get("sd"), 3),
            _f(v["free_play_deg"].get("mean"), 3), _f(v["free_play_deg"].get("sd"), 3),
            _f(v["encoder_deg"].get("mean"), 3), v["free_play_mm"].get("n", 0))
        for k, v in (summ.get("positions") or {}).items())
    L = ["# %s" % title, "", "| | |", "|---|---|", "| Run | `%s` |" % rid,
         "| Started / finished | %s / %s |" % (meta.get("started"), meta.get("finished")),
         "| Status | **%s** %s |" % (meta.get("status"), meta.get("message") or ""),
         "| Firmware / control | %s / %s |" % (sv.get("firmware_label"), p["mode"]),
         "| PID | %s: %s |" % (pid.get("kind"), pid_txt),
         "| Lever radius (axis to gauge contact) | %s mm |" % p["lever_r_mm"],
         "| Load applied | %s |" % (p.get("load_note") or "not described"),
         "| Positions x repeats | %s x %s |" % (p["positions"], p["repeats"]), "", "## Result", "", verdict or "No complete PLUS/MINUS pair.", "",
         "Free play = |gauge(PLUS) − gauge(MINUS)| per repeat; angle = free play / lever radius.", "",
         "| Position | Target | Free play mm | Free play deg | Encoder deg | n |", "|---|---|---|---|---|---|"]
    for k, v in (summ.get("positions") or {}).items():
        L.append("| %d | %s | %s ± %s | %s ± %s | %s | %s |" % (
            int(k) + 1, v["target"], _f(v["free_play_mm"].get("mean"), 3), _f(v["free_play_mm"].get("sd"), 3),
            _f(v["free_play_deg"].get("mean"), 3), _f(v["free_play_deg"].get("sd"), 3), _f(v["encoder_deg"].get("mean"), 3),
            v["free_play_mm"].get("n", 0)))
    rel_data = storage.rel(run_dir, rd)
    L += ["", "## Data", "", "* [samples.csv](%s/samples.csv) · [telemetry.csv](%s/telemetry.csv) · [meta.json](%s/meta.json) · "
          "[frames/](%s/frames/)" % ((rel_data,) * 4),
          "* Interactive: [%s_interactive.html](%s_interactive.html)" % (rid, rid), ""]
    with open(os.path.join(rd, rid + ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    data_rows = [dict(step=r["step"], pos_idx=r["pos_idx"], target=r["target"], repeat=r["repeat"], label=r["label"],
                      enc=r["enc_mean"], v=r.get("value_mm"), f=r.get("frame") or "", q=r.get("qwen_raw") or "",
                      m=r.get("manual_value") not in (None, ""), cur=r.get("current_ma"), t=r.get("temp"), ts=r.get("t_s")) for r in rows]
    reps = [dict(pos_idx=r["pos_idx"], repeat=r["repeat"], mm=r.get("free_play_mm"), deg=r.get("free_play_deg"),
                 enc=r.get("encoder_deg")) for r in (summ.get("repeats") or [])]
    # encoder trace from telemetry.csv (downsampled)
    tele = storage.read_csv(os.path.join(run_dir, "telemetry.csv"))
    stride = max(1, len(tele) // 5000)
    tele = tele[::stride]
    with open(os.path.join(storage.HERE, "static", "benchplot.js"), encoding="utf-8") as f:
        js = f.read()
    page = HTML_TEMPLATE
    cam = "camera %s, rotate %s" % (meta.get("camera"), meta.get("camera_rotate"))
    for k, v in dict(TITLE=html.escape(title), RID=rid, ASSETS=storage.rel(dst, rd), VERDICT=html.escape(verdict),
                     WHEN=html.escape("%s → %s" % (meta.get("started"), meta.get("finished"))),
                     STATUS=html.escape("%s %s" % (meta.get("status"), meta.get("message") or "")),
                     SETUP=html.escape("%s · %s · lever %s mm · load: %s · %s positions x %s repeats · %s" % (
                         sv.get("firmware_label"), p["mode"], p["lever_r_mm"], p.get("load_note") or "not described",
                         len(p["positions"]), p["repeats"], cam)),
                     PID=html.escape("%s: %s" % (pid.get("kind"), pid_txt)), POSROWS=pos_rows,
                     DIR=rel_data, ROWS=json.dumps(data_rows, separators=(",", ":")),
                     REPS=json.dumps(reps, separators=(",", ":")), FR=json.dumps(rel_data + "/frames"),
                     TELE=json.dumps(dict(t=[r["t_s"] for r in tele], pos=[r["pos"] for r in tele]), separators=(",", ":")),
                     LEVER=p["lever_r_mm"]).items():
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
<h1>@@TITLE@@</h1><div class="mut">T3 backlash (push test). Hover a point to see the gauge picture of that step. Drag to zoom, double-click to reset. <a href="@@RID@@.md">Text report</a></div>
<div class="card"><table><tr><th>Run</th><td><code>@@RID@@</code></td></tr><tr><th>When</th><td>@@WHEN@@</td></tr><tr><th>Status</th><td>@@STATUS@@</td></tr>
<tr><th>Setup</th><td>@@SETUP@@</td></tr><tr><th>PID</th><td>@@PID@@</td></tr></table></div>
<h2>Result</h2><div class="card"><p>@@VERDICT@@</p>
<table><tr><th>Position</th><th>Target</th><th>Free play mm</th><th>Free play deg</th><th>Encoder deg</th><th>n</th></tr>@@POSROWS@@</table>
<p class="mut">Free play = |gauge(PLUS) − gauge(MINUS)|; angle = free play / lever radius (@@LEVER@@ mm). Datasheet 0.5°; another user measured 0.87° (1.3 mm at 86 mm).</p></div>
<h2>Gauge reading per step</h2><div id="g1" style="height:340px"></div>
<h2>Free play per repeat (mm)</h2><div id="g2" style="height:280px"></div>
<h2>Encoder position during the test</h2><div id="g3" style="height:280px"></div>
<p class="mut">Data: <a href="@@DIR@@/samples.csv">samples.csv</a> · <a href="@@DIR@@/telemetry.csv">telemetry.csv</a> · <a href="@@DIR@@/meta.json">meta.json</a></p>
<script>@@JS@@</script>
<script>
const R=@@ROWS@@,REP=@@REPS@@,FR=@@FR@@,TE=@@TELE@@;
const COL={rest:'#6b7280',plus:'#2563eb',minus:'#d97706'};
const cap=r=>`step ${r.step+1} · position ${r.pos_idx+1} (${r.target}) · repeat ${r.repeat} · ${r.label.toUpperCase()} · ${r.v==null?'-':r.v.toFixed(2)} mm`+(r.q?` (Qwen saw ${r.q})`:'')+(r.m?' · typed':'')+` · encoder ${r.enc}`;
const ser=['rest','plus','minus'].map(l=>({name:l.toUpperCase(),color:COL[l],pts:R.filter(r=>r.label===l&&r.v!=null).map(r=>({x:r.step+1,y:r.v,img:r.f?FR+'/'+r.f:'',cap:cap(r)}))})).filter(s=>s.pts.length);
BenchPlot.draw(document.getElementById('g1'),ser,{dec:2,xtitle:'step',ytitle:'gauge (mm)',height:340});
const byPos={};REP.filter(r=>r.mm!=null).forEach(r=>{(byPos[r.pos_idx]=byPos[r.pos_idx]||[]).push(r);});
const PAL=['#2563eb','#d97706','#16a34a','#7c3aed','#dc2626'];
const s2=Object.keys(byPos).map((k,i)=>({name:'position '+(+k+1),color:PAL[i%5],pts:byPos[k].map(r=>{
  const p=R.find(x=>x.pos_idx===r.pos_idx&&x.repeat===r.repeat&&x.label==='plus');
  return {x:r.repeat,y:r.mm,img:p&&p.f?FR+'/'+p.f:'',cap:`position ${r.pos_idx+1} · repeat ${r.repeat} · free play ${r.mm} mm = ${r.deg} deg · encoder saw ${r.enc} deg`};})}));
BenchPlot.draw(document.getElementById('g2'),s2,{dec:3,xtitle:'repeat',ytitle:'free play (mm)',height:280});
BenchPlot.draw(document.getElementById('g3'),[{name:'encoder (counts)',color:'#9ca3af',mode:'lines',pts:TE.t.map((t,i)=>({x:t,y:TE.pos[i]}))}].concat(
  ['rest','plus','minus'].map(l=>({name:l.toUpperCase()+' recorded',color:COL[l],pts:R.filter(r=>r.label===l&&r.ts!=null).map(r=>({x:r.ts,y:r.enc,img:r.f?FR+'/'+r.f:'',cap:cap(r)}))})).filter(s=>s.pts.length)),
  {dec:1,xtitle:'time (s)',ytitle:'encoder counts',height:280});
</script></body></html>
"""
