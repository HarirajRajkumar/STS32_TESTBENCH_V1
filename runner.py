"""Repeatability test runner: T4.1a same-direction / T4.2b bidirectional, one servo, dial gauge + Qwen.

One cycle per direction d (+1 / -1):
    move to target - d*offset (approach point) -> arrive -> move to target -> arrive -> dwell
    -> read encoder/telemetry -> grab a fresh camera frame -> save crop -> queue Qwen read (background).
Motion never waits for Qwen unless reader == 'manual'.
Every telemetry poll (moves ~60 Hz, dwell 20 Hz) goes to telemetry.csv: temperature, current, voltage, load, position.
Files are written to local staging during the run and moved to data/<test>/<run>/ + docs/test_reports/repeatability at the end.
"""
import csv
import datetime as dt
import os
import queue
import re
import threading
import time

import camera as camlib
import stats
import storage
from sts import BusError, ExtSession
from vision import apply_decimals

CSV_COLS = storage.SAMPLE_COLS


def slug(s):
    return re.sub(r"[^A-Za-z0-9.]+", "-", s).strip("-").lower()


class Run:
    def __init__(self, app, p):
        self.app, self.p = app, p
        self.rows = []
        self.state = "starting"
        self.phase = ""
        self.message = ""
        self.stop_ev = threading.Event()
        self.estopped = False
        self.manual_ev = threading.Event()
        self.manual_value = None
        self.awaiting_manual = None
        self.cycle = 0
        self.cur_dir = ""
        self.total = int(p["cycles"])
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.ext = None
        self.t0 = time.monotonic()
        self.last_tel = None
        fw = app.servo_info or {}
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.test_id = "T4.2b" if p["pattern"] == "bidirectional" else "T4.1a"
        self.run_id = "%s_%s_%s_%s" % (stamp, self.test_id, slug(fw.get("firmware_label", "unknown")), p["mode"])
        if p.get("trial"):
            self.run_id += "_trial"
        self.dir = storage.staging_dir(self.run_id)
        self.final_dir = None
        self.publish_error = ""
        os.makedirs(os.path.join(self.dir, "frames"), exist_ok=True)
        s = app.settings
        self.meta = dict(run_id=self.run_id, test_id=self.test_id, started=dt.datetime.now().astimezone().isoformat(),
                         params=p, servo=fw, port=s.get("port"), camera=s.get("camera"),
                         camera_rotate=s.get("cam_rotate"), camera_flip=s.get("cam_flip"), roi=s.get("roi"),
                         vision=app.reader.describe() if app.reader else None,
                         ext_config=s.get("ext_config") if p["mode"] == "extension" else None, status="running")
        self._write_meta()
        self._tf = open(os.path.join(self.dir, "telemetry.csv"), "w", newline="", encoding="utf-8")
        self._tw = csv.DictWriter(self._tf, fieldnames=storage.TELEM_COLS, extrasaction="ignore")
        self._tw.writeheader()
        self._t_flush = time.monotonic()
        self.thread = threading.Thread(target=self._main, name="run", daemon=True)
        self.reader_thread = threading.Thread(target=self._reader_loop, name="qwen", daemon=True)

    # ---------------------------------------------------------------- files
    def _write_meta(self):
        storage.write_json(os.path.join(self.dir, "meta.json"), self.meta)

    def save(self):
        with self.lock:
            rows = [dict(r) for r in self.rows]
        storage.write_samples(self.dir, rows)
        return stats.summarise(rows, float(self.p.get("lever_r_mm") or 0), int(self.p.get("skip_first") or 0))

    def _log(self, phase, goal, src, d):
        """d: legacy telemetry dict or extension feedback dict."""
        now = time.monotonic()
        row = dict(t_s=round(now - self.t0, 3), t_iso=dt.datetime.now().isoformat(timespec="milliseconds"),
                   cycle=self.cycle, dir=self.cur_dir, phase=phase, goal=goal, pos=d.get("pos"), source=src,
                   temp=d.get("temp"), voltage=d.get("voltage"))
        if src == "legacy":
            row.update(speed=d.get("speed"), load=d.get("load"), current_ma=d.get("current_ma"), moving=d.get("moving"))
        else:
            row.update(speed=round(d.get("vel", 0), 1), load=d.get("output"), current_raw=d.get("current_raw"),
                       moving=int(bool(d.get("moving"))))
        self._tw.writerow(row)
        self.last_tel = row
        if now - self._t_flush > 1.0:
            self._tf.flush()
            self._t_flush = now

    def _poll(self, phase, goal):
        a, sid = self.app, self.p["id"]
        if self.ext:
            fb = a.bus.ext_feedback(sid)
            self._log(phase, goal, "ext", fb)
            return fb
        t = a.bus.telemetry(sid)
        self._log(phase, goal, "legacy", t)
        return t

    # ---------------------------------------------------------------- motion
    def _move(self, pos, label, phase):
        a, p, sid = self.app, self.p, self.p["id"]
        self.phase = label
        t0 = time.monotonic()
        if self.ext:
            seq = self.ext.move(pos, p["speed"])
        else:
            a.bus.legacy_move(sid, pos, p["speed"], p["acc_reg"])
        time.sleep(0.05)
        ok_n, still_since = 0, None
        while time.monotonic() - t0 < p["move_timeout_s"]:
            if self.stop_ev.is_set():
                return "stopped", 0
            fb = self._poll(phase, pos)
            if self.ext:
                if not (fb["drive"] and fb["ready"]):
                    raise BusError("extension drive dropped (flags 0x%04X, fault 0x%02X)" % (fb["flags"], fb["fault"]))
                still = fb["target_seq"] == seq and not fb["moving"]
            else:
                still = fb["moving"] == 0
            done = still and abs(fb["pos"] - pos) <= p["tol"]
            ok_n = ok_n + 1 if done else 0
            if ok_n >= 3:
                return "ok", round(1000 * (time.monotonic() - t0))
            still_since = (still_since or time.monotonic()) if still else None
            if still_since and time.monotonic() - still_since > 0.4:
                return "stalled", round(1000 * (time.monotonic() - t0))   # stopped outside tolerance: recorded, not hidden
            time.sleep(0.015)
        return "timeout", round(1000 * (time.monotonic() - t0))

    def _wobble(self, target, pre, sgn):
        """Optional disturbance before the measured approach (wobble_n = 0: off).
        path : wiggle +/- wobble_amp around the midpoint between the away position and the gauge.
        gauge: touch the gauge and lift off by wobble_amp, wobble_n times.
        The final move to the target and the reading are unchanged, so the readings show whether the
        touch point moves after a disturbed approach. Every wobble move is in telemetry.csv (phase 'wobble')."""
        n, amp = int(self.p.get("wobble_n") or 0), int(self.p.get("wobble_amp") or 0)
        if n <= 0 or amp <= 0:
            return "ok"
        if self.p.get("wobble_where") == "gauge":
            moves = [target, target - sgn * amp] * n
        else:
            mid = (pre + target) // 2
            moves = [mid + sgn * amp, mid - sgn * amp] * n
        for i, pos in enumerate(moves):
            r, _ = self._move(pos, "wobble %d/%d -> %d" % (i // 2 + 1, n, pos), "wobble")
            if r == "stopped":
                return r
        return "ok"

    def _temp_confirmed(self, limit):
        """The temperature sensor sometimes returns a single wrong value (76 / 150 C while the servo is at 36 C).
        Stop only if three more reads over 0.4 s are also at or above the limit."""
        for _ in range(3):
            time.sleep(0.2)
            try:
                if self.app.bus.telemetry(self.p["id"])["temp"] < limit:
                    return False
            except BusError:
                return False
        return True

    def _dwell(self, s, goal):
        end = time.monotonic() + s
        while time.monotonic() < end and not self.stop_ev.is_set():
            self._poll("dwell", goal)
            time.sleep(0.05)

    # ---------------------------------------------------------------- main loop
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
            self.state = "running"
            dirs = ["+", "-"] if p["pattern"] == "bidirectional" else [p["direction"]]
            target, off = int(p["target"]), int(p["offset"])
            for c in range(1, self.total + 1):
                self.cycle = c
                for d in dirs:
                    if self.stop_ev.is_set():
                        break
                    self.cur_dir = d
                    sgn = 1 if d == "+" else -1
                    pre = target - sgn * off
                    r1, _ = self._move(pre, "approach point %d (%s)" % (pre, d), "approach")
                    if r1 == "stopped":
                        break
                    if self._wobble(target, pre, sgn) == "stopped":
                        break
                    r2, ms = self._move(target, "to target %d from %s" % (target, d), "target")
                    if r2 == "stopped":
                        break
                    self.phase = "dwell %.1fs" % p["dwell_s"]
                    self._dwell(p["dwell_s"], target)
                    t_settled = time.time()
                    tel = a.bus.telemetry(sid)
                    self._log("sample", target, "legacy", tel)
                    if self.ext:
                        fb = self._poll("sample", target)
                        tel["pos"] = round(fb["pos"], 2)
                    if tel["temp"] >= p["max_temp_c"] and self._temp_confirmed(p["max_temp_c"]):
                        self.message = "temperature %d C >= limit %d C" % (tel["temp"], p["max_temp_c"])
                        self.stop_ev.set()
                    row = dict(run_id=self.run_id, cycle=c, dir=d, t_iso=dt.datetime.now().isoformat(timespec="milliseconds"),
                               target=target, approach_from=pre, pos=tel["pos"], err_counts=round(tel["pos"] - target, 2),
                               current_ma=tel["current_ma"], load=tel["load"], temp=tel["temp"], voltage=tel["voltage"],
                               arrive=r2 if r1 == "ok" else "%s/%s" % (r1, r2), arrive_ms=ms, qwen_status="none",
                               manual_value=None, exclude=False)
                    jpg = None
                    img, _ = a.camera.fresh_frame(t_settled) if a.camera.cap else (None, 0)
                    if img is not None:
                        crop = camlib.crop_roi(img, a.settings.get("roi"))
                        fn = "c%04d_%s.jpg" % (c, "p" if d == "+" else "m")
                        jpg = camlib.jpeg_bytes(crop)
                        with open(os.path.join(self.dir, "frames", fn), "wb") as f:
                            f.write(jpg)
                        row["frame"] = fn
                    with self.lock:
                        self.rows.append(row)
                        idx = len(self.rows) - 1
                    if p["reader"] == "qwen" and jpg:
                        row["qwen_status"] = "pending"
                        self.q.put((idx, jpg))
                    elif p["reader"] == "manual":
                        self._wait_manual(idx)
                if self.stop_ev.is_set():
                    break
                if c % 5 == 0:
                    self.save()
            self.state = "stopped" if self.stop_ev.is_set() else "finishing"
        except BusError as e:
            self.message = "bus error: %s" % e
            self.state = "error"
        except Exception as e:                       # never leave the motor in an unknown session silently
            self.message = "%s: %s" % (type(e).__name__, e)
            self.state = "error"
        finally:
            self.phase = "holding position" if not self.ext else "ending extension session"
            if self.ext:
                try:
                    self.ext.stop(to_legacy=True)       # torque off + back to legacy mode
                    if not self.estopped:
                        a.bus.legacy_hold_here(sid)     # keep the lever where it is instead of dropping it
                except Exception as e:
                    self.message += " | session stop: %s" % e
            a.runner_owns_bus = False
            self.phase = "waiting for Qwen (%d left)" % self.q.qsize()
            self.q.put(None)
            self.reader_thread.join(timeout=300)
            try:
                self._tf.close()
            except Exception:
                pass
            self.save()
            if self.state != "error":
                self.state = "stopped" if self.stop_ev.is_set() else "done"
            self.meta.update(status=self.state, finished=dt.datetime.now().astimezone().isoformat(),
                             message=self.message, cycles_done=self.cycle)
            self._write_meta()
            self.phase = "saving report + Excel"
            try:
                storage.finalize(self.dir, write_index=False)
                dest = storage.publish(self.dir)          # staging -> data/<test>/<run>, report -> docs/test_reports
                self.dir = self.final_dir = dest
            except Exception as e:
                self.publish_error = "could not move to project folder (%s); data kept in %s" % (e, self.dir)
                self.message = (self.message + " | " + self.publish_error).strip(" |")
            self.phase = ""

    def _wait_manual(self, idx):
        self.awaiting_manual = idx
        self.phase = "type the gauge reading"
        self.manual_ev.clear()
        while not self.manual_ev.wait(0.1):
            if self.stop_ev.is_set():
                break
        with self.lock:
            self.rows[idx]["manual_value"] = self.manual_value
        self.awaiting_manual = None

    def _reader_loop(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            idx, jpg = item
            dec = self.p.get("gauge_decimals")
            res = apply_decimals(self.app.reader.read(jpg, dec), dec)
            with self.lock:
                r = self.rows[idx]
                r.update(qwen_status=res["status"], qwen_value=res["value"], qwen_raw=res.get("raw_text"),
                         qwen_conf=res.get("confidence"), qwen_ms=res.get("ms"), qwen_notes=res.get("notes"))

    def start(self):
        self.reader_thread.start()
        self.thread.start()

    def view(self, since=0):
        with self.lock:
            rows = [dict(r) for r in self.rows[since:]]
            all_rows = [dict(r) for r in self.rows]
        return dict(run_id=self.run_id, test_id=self.test_id, state=self.state, phase=self.phase, message=self.message,
                    cycle=self.cycle, total=self.total, rows=rows, n_rows=len(all_rows),
                    awaiting_manual=self.awaiting_manual, trial=bool(self.p.get("trial")),
                    planned_cycles=self.p.get("planned_cycles"), qwen_backlog=self.q.qsize(), last_tel=self.last_tel,
                    data_dir=storage.rel(self.final_dir, storage.PROJECT) if self.final_dir else None,
                    report=storage.rel(os.path.join(storage.report_dir(self.test_id, self.p.get("trial")), self.run_id + ".md"),
                                       storage.PROJECT) if self.final_dir else None,
                    summary=stats.summarise(all_rows, float(self.p.get("lever_r_mm") or 0),
                                            int(self.p.get("skip_first") or 0)))
