"""STS3215 Repeatability Bench -- Feetech factory firmware vs RoboNine STS32XX firmware.

    python app.py            -> http://127.0.0.1:5077
One browser page: live camera + gauge box, servo connect/jog/torque, run T4.1a / T4.2b, results + firmware comparison.
"""
import base64
import json
import os
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory
from serial.tools import list_ports

import backlash
import camera as camlib
import hold
import stats
import storage
from runner import Run
from sts import DEFAULT_EXT_CONFIG, MIN_SUPPLY_V, R_LOCK, Bus, BusError
from vision import QwenReader, api_key, apply_decimals, fixed_decimals

APP_VERSION = "1.9.0"     # 1.9.0: T3 backlash push test tab (backlash.py)
                          # 1.8.0: reports sorted into hold-pid/ and repeatability/, library page 00_library.html
                          # 1.7.1: temperature limit needs a confirmed reading (bad single reads no longer stop a hold or run)
                          # 1.7.0: Hold & PID test tab (hold.py)
                          # 1.6.0: interactive plotly charts (hover = camera picture) live, per run, comparison + <run>_interactive.html reports
                          # 1.5.1: gauge readings always shown as x.xx; Qwen raw text shown alongside
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")
FLASH_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "firmware", "STS32XX-Firmware", "STS32XX-Firmware",
                                          "tools", "flash"))
FIRMWARES = {   # which -> manifest, relative to FLASH_DIR
    "robonine": os.path.join("releases", "custom", "release.json"),
    "feetech": os.path.join("factory", "release.json"),
}
ROBONINE_ZIP = "https://github.com/roboninecom/STS32XX-Firmware/releases/download/v0.1-preview/custom-v9-e44c0e6.zip"
DEFAULT_SETTINGS = dict(port="COM7", baud=1000000, id=1, camera=0, cam_rotate=0, cam_flip="none", roi=None,
                        vision_url=None, vision_model=None,
                        soft_min=0, soft_max=4095, ext_config=DEFAULT_EXT_CONFIG,
                        run=dict(mode="legacy", pattern="same", direction="+", target=2048, offset=60, cycles=100,
                                 speed=1000, accel=5000, dwell_s=1.5, tol=3, move_timeout_s=6, reader="qwen",
                                 lever_r_mm=50.0, skip_first=1, max_temp_c=65, note="", gauge_decimals=2,
                                 wobble_n=0, wobble_amp=20, wobble_where="path"))


class AppState:
    def __init__(self):
        self.settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    saved = json.load(f)
                for k, v in saved.items():
                    if isinstance(v, dict) and isinstance(self.settings.get(k), dict):
                        self.settings[k].update(v)
                    else:
                        self.settings[k] = v
            except Exception:
                pass
        self.recovered = storage.recover()       # unfinished / old-layout runs -> data/ + docs/test_reports
        self.bus = None
        self.servo_info = None
        self.telem = None
        self.telem_err = ""
        self.runner_owns_bus = False
        self.run = None
        self.hold = None                          # Hold & PID test (hold.py)
        self.backlash = None                      # T3 backlash push test (backlash.py)
        self.sweep = None                         # limit sweep: {"min","max","n"} while the lever is moved by hand
        self.calib = self.settings.get("calib") or {}
        self.camera = camlib.Camera()
        self.camera.rotate = int(self.settings.get("cam_rotate") or 0)
        self.camera.flip = self.settings.get("cam_flip") or "none"
        self.reader = QwenReader(self.settings.get("vision_url"), self.settings.get("vision_model"))
        self.fw_job = None                        # firmware flash: dict(which, state, log[], code)
        threading.Thread(target=self._poll, name="telemetry", daemon=True).start()

    def save_settings(self):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=2)

    def _poll(self):
        while True:
            time.sleep(0.15)
            if not self.bus or not self.servo_info or self.runner_owns_bus:
                continue
            try:
                self.telem = self.bus.telemetry(self.settings["id"])
                self.telem["t"] = time.time()
                sw = self.sweep
                if sw is not None:
                    p = self.telem["pos"]
                    sw["min"] = p if sw["min"] is None else min(sw["min"], p)
                    sw["max"] = p if sw["max"] is None else max(sw["max"], p)
                    sw["n"] += 1
                self.telem_err = ""
            except Exception as e:
                self.telem_err = str(e)

    def running(self):
        """True while a test run OR a firmware flash owns the port (every motion endpoint checks this)."""
        return ((self.run is not None and self.run.thread.is_alive()) or self.flashing() or
                (self.hold is not None and self.hold.alive()) or (self.backlash is not None and self.backlash.alive()))

    def flashing(self):
        return bool(self.fw_job and self.fw_job["state"] == "running")


S = AppState()
app = Flask(__name__, static_folder=os.path.join(HERE, "static"))


def ok(**kw):
    kw.setdefault("ok", True)
    return jsonify(kw)


def fail(msg, code=400):
    return jsonify(ok=False, error=msg), code


def no_power():
    """Fresh voltage read. Without the motor supply the servo chip runs on signal-line leakage: its position,
    model and limit registers read garbage, so calibrating or flashing then would store garbage."""
    try:
        v = S.bus.telemetry(S.settings["id"])["voltage"]
    except Exception as e:
        return fail("cannot read the servo: %s" % e)
    if v < MIN_SUPPLY_V:
        if S.servo_info and S.servo_info.get("settings_wiped"):
            return fail("The servo's settings area is wiped (model 9.255, limits 65535, voltage alarm). Restore its "
                        "settings from the backup in firmware/.../backups/settings before anything else.")
        return fail("The servo reads %.1f V with a voltage alarm. Check the 12 V supply first; if the supply is on, "
                    "the servo's settings may be wiped (see the Firmware tab)." % v)
    return None


def body():
    return request.get_json(silent=True) or {}


# ------------------------------------------------------------------ page + video
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/video.mjpg")
def video():
    return Response(S.camera.mjpeg(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/crop.jpg")
def crop_jpg():
    img, _ = S.camera.latest()
    if img is None:
        return fail("no camera frame", 404)
    return Response(camlib.jpeg_bytes(camlib.crop_roi(img, S.settings.get("roi"))), mimetype="image/jpeg")


# ------------------------------------------------------------------ state
@app.route("/api/state")
def state():
    since = int(request.args.get("since", 0))
    s = {k: v for k, v in S.settings.items()}
    return ok(version=APP_VERSION, fw_job=S.fw_job, sweep=S.sweep, calib=S.calib, connected=S.bus is not None, servo=S.servo_info, telem=S.telem, telem_err=S.telem_err,
              settings=s, camera_open=S.camera.cap is not None, camera_error=S.camera.error,
              vision=S.reader.describe(), vision_key=bool(api_key()),
              run=S.run.view(since) if S.run else None, running=S.running(),
              hold=S.hold.view(int(request.args.get("hsince", 0))) if S.hold else None,
              backlash=S.backlash.view() if S.backlash else None)


@app.route("/api/ports")
def ports():
    return ok(ports=[dict(device=p.device, desc=p.description) for p in list_ports.comports()])


@app.route("/api/cameras")
def cameras():
    """Cameras OpenCV can open, the one in use (if any), and the names Windows lists. Scanning takes ~2 s."""
    cur = S.camera.index if S.camera.cap is not None else None
    found = camlib.list_cameras(skip=cur)
    if cur is not None:
        img, _ = S.camera.latest()
        found.append(dict(index=cur, w=int(img.shape[1]) if img is not None else 0,
                          h=int(img.shape[0]) if img is not None else 0, dark=False, in_use=True))
    found.sort(key=lambda c: c["index"])
    return ok(cameras=found, current=cur if cur is not None else S.settings.get("camera"),
              windows_names=camlib.windows_camera_names())


@app.route("/api/connect", methods=["POST"])
def connect():
    if S.running():
        return fail("a run is active")
    b = body()
    port, baud = b.get("port") or S.settings["port"], int(b.get("baud") or S.settings["baud"])
    if S.bus:
        S.bus.close()
        S.bus = None
    try:
        S.bus = Bus(port, baud)
    except Exception as e:
        return fail("cannot open %s: %s (close FT SCServo Debug first)" % (port, e))
    S.settings.update(port=port, baud=baud)
    S.save_settings()
    sid = int(b.get("id") or S.settings["id"])
    return select_id(sid)


def select_id(sid):
    try:
        S.servo_info = S.bus.identify(sid)
    except BusError as e:
        S.servo_info = None
        return fail("port open, but ID %d did not answer: %s. Try Scan." % (sid, e))
    S.servo_info["id"] = sid
    S.settings["id"] = sid
    lo, hi = S.servo_info["min_limit"], S.servo_info["max_limit"]
    if lo < hi:
        S.settings["soft_min"], S.settings["soft_max"] = lo, hi
    S.save_settings()
    return ok(servo=S.servo_info)


@app.route("/api/select", methods=["POST"])
def select():
    if not S.bus or S.running():
        return fail("not connected / run active")
    return select_id(int(body().get("id", 1)))


@app.route("/api/scan", methods=["POST"])
def scan():
    if not S.bus or S.running():
        return fail("not connected / run active")
    S.runner_owns_bus = True
    try:
        return ok(ids=S.bus.scan(range(0, 21)))
    finally:
        S.runner_owns_bus = False


@app.route("/api/disconnect", methods=["POST"])
def disconnect():
    if S.running():
        return fail("stop the run first")
    if S.bus:
        S.bus.close()
        S.bus, S.servo_info, S.telem = None, None, None
    return ok()


@app.route("/api/torque", methods=["POST"])
def torque():
    if not S.servo_info or S.running():
        return fail("not connected / run active")
    on = bool(body().get("on"))
    if on and (np := no_power()):
        return np
    try:
        if on:
            S.bus.legacy_hold_here(S.settings["id"])
        else:
            S.bus.torque(S.settings["id"], False)
    except BusError as e:
        return fail(str(e))
    return ok()


@app.route("/api/move", methods=["POST"])
def move():
    if not S.servo_info or S.running():
        return fail("not connected / run active")
    if (np := no_power()):
        return np
    b = body()
    pos = int(b["pos"])
    if not S.settings["soft_min"] <= pos <= S.settings["soft_max"]:
        return fail("%d is outside soft limits %d..%d" % (pos, S.settings["soft_min"], S.settings["soft_max"]))
    speed = int(b.get("speed") or S.settings["run"]["speed"])
    acc = int(b.get("accel") or S.settings["run"]["accel"])
    try:
        S.bus.legacy_move(S.settings["id"], pos, speed, max(1, min(254, round(acc / 100))))
    except BusError as e:
        return fail(str(e))
    return ok()


@app.route("/api/calib/center", methods=["POST"])
def calib_center():
    """Step 1: lever pointing straight up -> this position becomes 2048 (saved in the servo)."""
    if not S.servo_info or S.running():
        return fail("not connected / run active")
    if (np := no_power()):
        return np
    try:
        pos, corr = S.bus.calibrate_center(S.settings["id"])
    except BusError as e:
        return fail(str(e))
    good = abs(pos - 2048) <= 2
    S.calib = dict(center_pos=pos, position_correction=corr, center_ok=good,
                   center_time=time.strftime("%Y-%m-%d %H:%M:%S"), firmware=S.servo_info.get("firmware_label"))
    S.settings["calib"] = S.calib
    S.save_settings()
    if not good:
        return fail(f"servo reads {pos!s} after centring, expected 2048 (offset {corr!s}). "
                    "Is the settings lock on / alarm active?")
    return ok(pos=pos, position_correction=corr)


@app.route("/api/calib/sweep", methods=["POST"])
def calib_sweep():
    """Step 2: torque off, move the lever by hand back to its stop and forward onto the gauge; min/max are tracked."""
    if not S.servo_info or S.running():
        return fail("not connected / run active")
    if (np := no_power()):
        return np
    act = body().get("action")
    if act == "start":
        try:
            S.bus.torque(S.settings["id"], False)
        except BusError as e:
            return fail(str(e))
        S.sweep = dict(min=None, max=None, n=0, t=time.time())
        return ok(sweep=S.sweep)
    if act == "cancel":
        S.sweep = None
        return ok()
    if act == "save":
        sw, b = S.sweep, body()
        if not sw or sw["min"] is None or sw["max"] - sw["min"] < 20:
            return fail("move the lever end to end first (travel < 20 counts)")
        margin = int(b.get("margin", 10))
        lo, hi = int(sw["min"]) + margin, int(sw["max"]) - margin
        S.settings["soft_min"], S.settings["soft_max"] = lo, hi
        S.calib.update(sweep_min=sw["min"], sweep_max=sw["max"], margin=margin, soft_min=lo, soft_max=hi,
                       limits_time=time.strftime("%Y-%m-%d %H:%M:%S"), limits_written_to_servo=False)
        msg = "soft limits %d..%d saved in the bench" % (lo, hi)
        if b.get("write_servo"):
            try:
                S.bus.write_limits(S.settings["id"], lo, hi)
                S.servo_info["min_limit"], S.servo_info["max_limit"] = lo, hi
                S.calib["limits_written_to_servo"] = True
                msg += " and written to the servo (0x09-0x0C)"
            except BusError as e:
                msg += "; writing to the servo FAILED: %s" % e
        S.settings["calib"] = S.calib
        S.save_settings()
        S.sweep = None
        return ok(message=msg, soft_min=lo, soft_max=hi)
    return fail("action start|save|cancel")


@app.route("/api/calib/mark", methods=["POST"])
def calib_mark():
    """Positions step: torque off, lever placed by hand. which = away (far end, away from the gauge) | gauge."""
    if not S.servo_info or S.running():
        return fail("not connected / run active")
    if (np := no_power()):
        return np
    which = body().get("which")
    if which not in ("away", "gauge"):
        return fail("which = away | gauge")
    try:
        pos = S.bus.telemetry(S.settings["id"])["pos"]
    except BusError as e:
        return fail(str(e))
    S.calib[which + "_pos"] = pos
    S.calib[which + "_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    S.calib.pop("positions_saved", None)
    S.settings["calib"] = S.calib
    S.save_settings()
    return ok(pos=pos)


@app.route("/api/calib/positions", methods=["POST"])
def calib_positions():
    """From the two marks: test position = gauge, start of each cycle = away end (minus margin),
    approach direction = from the away side, limits = away end .. gauge + overtravel."""
    if not S.servo_info or S.running():
        return fail("not connected / run active")
    if (np := no_power()):
        return np
    b = body()
    away, gauge = S.calib.get("away_pos"), S.calib.get("gauge_pos")
    if away is None or gauge is None:
        return fail("mark both positions first")
    margin, over = int(b.get("margin", 10)), int(b.get("overtravel", 30))
    if abs(gauge - away) < 2 * margin + 20:
        return fail("away end and gauge position are only %d counts apart" % abs(gauge - away))
    d = 1 if away < gauge else -1
    start = away + d * margin
    lo, hi = sorted((start, gauge + d * over))
    lo, hi = max(0, lo), min(4095, hi)
    S.settings["soft_min"], S.settings["soft_max"] = lo, hi
    run = S.settings["run"]
    run.update(target=gauge, offset=abs(gauge - start), direction="+" if d > 0 else "-", pattern="same")
    S.calib.update(soft_min=lo, soft_max=hi, margin=margin, overtravel=over, start_pos=start, target=gauge,
                   positions_saved=True, limits_written_to_servo=False,
                   limits_time=time.strftime("%Y-%m-%d %H:%M:%S"))
    msg = "Each cycle: go to %d, come back to %d, read the gauge." % (start, gauge)
    if b.get("write_servo", True):
        try:
            S.bus.write_limits(S.settings["id"], lo, hi)
            S.servo_info["min_limit"], S.servo_info["max_limit"] = lo, hi
            S.calib["limits_written_to_servo"] = True
        except BusError as e:
            msg += " (Writing the limits into the servo failed: %s)" % e
    S.settings["calib"] = S.calib
    S.save_settings()
    return ok(message=msg, start=start, target=gauge, soft_min=lo, soft_max=hi)


@app.route("/api/estop", methods=["POST"])
def estop():
    if S.run and S.running():
        S.run.estopped = True
        S.run.stop_ev.set()
    if S.hold and S.hold.alive():
        S.hold.estopped = True
        S.hold.stop_ev.set()
    if S.backlash and S.backlash.alive():
        S.backlash.estopped = True
        S.backlash.stop_ev.set()
    errs = []
    for _ in range(3):
        try:
            if S.bus and S.servo_info:
                S.bus.torque(S.settings["id"], False)
        except Exception as e:
            errs.append(str(e))
        time.sleep(0.08)
    return ok(errors=errs)


@app.route("/api/limits", methods=["POST"])
def limits():
    b = body()
    S.settings["soft_min"], S.settings["soft_max"] = int(b["soft_min"]), int(b["soft_max"])
    S.save_settings()
    return ok()


# ------------------------------------------------------------------ camera + vision
@app.route("/api/camera", methods=["POST"])
def cam_open():
    idx = int(body().get("index", S.settings["camera"]))
    try:
        S.camera.open(idx)
    except Exception as e:
        return fail(str(e))
    S.settings["camera"] = idx
    S.save_settings()
    return ok()


@app.route("/api/roi", methods=["POST"])
def roi():
    b = body()
    S.settings["roi"] = {k: float(b[k]) for k in ("x", "y", "w", "h")} if b.get("w") else None
    S.save_settings()
    return ok()


@app.route("/api/camera/orient", methods=["POST"])
def cam_orient():
    b = body()
    rot = int(b.get("rotate", 0)) % 360
    flip = b.get("flip", "none")
    if rot not in (0, 90, 180, 270) or flip not in ("none", "h", "v"):
        return fail("rotate 0/90/180/270, flip none/h/v")
    changed = (rot, flip) != (S.camera.rotate, S.camera.flip)
    S.camera.rotate, S.camera.flip = rot, flip
    S.settings["cam_rotate"], S.settings["cam_flip"] = rot, flip
    if changed:
        S.settings["roi"] = None                 # the box was drawn on the old orientation: draw it again
    S.save_settings()
    return ok(roi_cleared=changed)


@app.route("/api/vision", methods=["POST"])
def vision_cfg():
    b = body()
    S.settings["vision_url"] = b.get("url") or None
    S.settings["vision_model"] = b.get("model") or None
    S.reader = QwenReader(S.settings["vision_url"], S.settings["vision_model"])
    S.save_settings()
    return ok(vision=S.reader.describe())


@app.route("/api/vision/test", methods=["POST"])
def vision_test():
    img, _ = S.camera.latest()
    if img is None:
        return fail("camera is not open")
    jpg = camlib.jpeg_bytes(camlib.crop_roi(img, S.settings.get("roi")))
    dec = S.settings["run"].get("gauge_decimals")
    res = apply_decimals(S.reader.read(jpg, dec), dec)
    res["decimals"] = dec
    res["crop_b64"] = base64.b64encode(jpg).decode("ascii")
    return ok(result=res)


@app.route("/api/ext_config", methods=["POST"])
def ext_config():
    cfg = body().get("config") or {}
    merged = dict(DEFAULT_EXT_CONFIG)
    merged.update({k: int(v) for k, v in cfg.items() if k in DEFAULT_EXT_CONFIG})
    S.settings["ext_config"] = merged
    S.save_settings()
    return ok(config=merged)


# ------------------------------------------------------------------ runs
@app.route("/api/run/start", methods=["POST"])
def run_start():
    if S.running():
        return fail("a run is already active")
    if not S.servo_info:
        return fail("connect the servo first")
    if (np := no_power()):
        return np
    p = dict(S.settings["run"])
    p.update(body())
    trial = bool(p.pop("trial", False))
    try:
        for k in ("target", "offset", "cycles", "speed", "accel", "tol", "skip_first", "max_temp_c"):
            p[k] = int(float(p[k]))
        for k in ("dwell_s", "move_timeout_s", "lever_r_mm"):
            p[k] = float(p[k])
        p["gauge_decimals"] = None if p.get("gauge_decimals") in (None, "", "auto") else int(p["gauge_decimals"])
        p["wobble_n"], p["wobble_amp"] = int(float(p.get("wobble_n") or 0)), int(float(p.get("wobble_amp") or 0))
    except (KeyError, ValueError) as e:
        return fail("bad parameter: %s" % e)
    p["id"] = S.settings["id"]
    p["acc_reg"] = max(1, min(254, round(p["accel"] / 100)))
    lo, hi = S.settings["soft_min"], S.settings["soft_max"]
    if p["pattern"] == "same":                  # only the gauge position and the away position are visited
        d = 1 if p["direction"] == "+" else -1
        need = (p["target"], p["target"] - d * p["offset"])
    else:
        need = (p["target"] - p["offset"], p["target"] + p["offset"])
    bad_pos = [x for x in need if not lo <= x <= hi]
    if bad_pos:
        return fail("position %d is outside the allowed range %d..%d. Redo step 4 (Positions)." % (bad_pos[0], lo, hi))
    if p["offset"] < 1 or p["cycles"] < 1:
        return fail("offset and cycles must be >= 1")
    if p["wobble_n"] > 0:
        if p["wobble_where"] not in ("path", "gauge"):
            return fail("wobble place must be path or gauge")
        if p["wobble_n"] > 20 or not 1 <= p["wobble_amp"] < p["offset"] // 2:
            return fail("wobble: 1..20 wiggles, size 1..%d counts (less than half the back-off)" % (p["offset"] // 2 - 1))
    p["app_version"] = APP_VERSION
    if p["mode"] == "extension" and S.servo_info.get("firmware_family") != "robonine":
        return fail("extension mode needs the RoboNine v9 firmware (servo reports %s)" % S.servo_info["firmware_label"])
    if p["reader"] in ("qwen", "manual") and S.camera.cap is None and p["reader"] == "qwen":
        return fail("open the camera first (or choose reader 'manual' / 'none')")
    S.settings["run"] = {k: v for k, v in p.items() if k in DEFAULT_SETTINGS["run"]}
    S.save_settings()
    if S.sweep is not None:
        return fail("finish or cancel the limit sweep first")
    p["calib"] = S.calib
    if trial:
        p["trial"], p["planned_cycles"], p["cycles"] = True, p["cycles"], min(3, p["cycles"])
    S.run = Run(S, p)
    S.run.start()
    return ok(run_id=S.run.run_id)


@app.route("/api/run/defaults", methods=["POST"])
def run_defaults():
    for k, v in body().items():
        if k in DEFAULT_SETTINGS["run"]:
            S.settings["run"][k] = v
        if k == "target":
            S.calib.update(target=int(v), target_time=time.strftime("%Y-%m-%d %H:%M:%S"))
            S.settings["calib"] = S.calib
    S.save_settings()
    return ok()


@app.route("/api/run/stop", methods=["POST"])
def run_stop():
    if S.run:
        S.run.stop_ev.set()
    return ok()


@app.route("/api/run/manual", methods=["POST"])
def run_manual():
    if not S.run or S.run.awaiting_manual is None:
        return fail("no reading requested")
    v = body().get("value")
    S.run.manual_value = None if v in (None, "") else float(v)
    S.run.manual_ev.set()
    return ok()


@app.route("/api/run/edit", methods=["POST"])
def run_edit():
    """Operator correction of one reading (Qwen 8<->0, upside-down 2.36 -> 6.22 ...) or exclude it.
    Rewrites samples.csv, summary, run.xlsx and the report."""
    b = body()
    rid, idx = b["run_id"], int(b["idx"])
    mv = b.get("manual_value")
    mv = None if mv in (None, "") else float(mv)
    live = S.run if (S.run and S.run.run_id == rid) else None
    if live:
        with live.lock:
            live.rows[idx]["manual_value"] = mv
            live.rows[idx]["exclude"] = bool(b.get("exclude"))
        live.save()
        if not live.final_dir:                  # still running: the report is written at the end
            return ok()
    d = storage.find_run(rid)
    if not d:
        return fail("run not found")
    if not live:
        meta, rows, _ = storage.load_run(d)
        rows[idx]["manual_value"] = mv
        rows[idx]["exclude"] = bool(b.get("exclude"))
        storage.write_samples(d, rows)
    _, warn = storage.finalize(d)
    return ok(warning=warn)


@app.route("/api/run/decimals", methods=["POST"])
def run_decimals():
    """Finished run: re-place the decimal point in every Qwen reading from its raw digits (the gauge shows a fixed
    number of decimals). Manual corrections are kept. Rewrites CSV, Excel and report."""
    b = body()
    rid, dec = b.get("run_id"), int(b.get("decimals", 2))
    if S.run and S.run.run_id == rid and S.running():
        return fail("wait until the run has finished")
    d = storage.find_run(rid)
    if not d:
        return fail("run not found")
    meta, rows, _ = storage.load_run(d)
    n = 0
    for r in rows:
        v = fixed_decimals(r.get("qwen_raw"), dec) if r.get("qwen_status") == "ok" else None
        if v is not None:
            r["qwen_value"] = v
            n += 1
    storage.write_samples(d, rows)
    meta.setdefault("params", {})["gauge_decimals"] = dec
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    _, warn = storage.finalize(d)
    return ok(fixed=n, warning=warn)


@app.route("/api/runs")
def runs():
    out = []
    for d in storage.all_run_dirs():
        m = storage.read_json(os.path.join(d, "meta.json"))
        if not m:
            continue
        p = m.get("params") or {}
        out.append(dict(run_id=m.get("run_id"), test_id=m.get("test_id"), started=m.get("started"),
                        status=m.get("status"), firmware=(m.get("servo") or {}).get("firmware_label"),
                        mode=p.get("mode"), note=p.get("note"), data_dir=m.get("data_dir"), report=m.get("report")))
    out.sort(key=lambda r: r.get("started") or "", reverse=True)
    return ok(runs=out, recovered=S.recovered)


@app.route("/api/runs/<rid>")
def run_detail(rid):
    if S.run and S.run.run_id == rid and S.running():
        v = S.run.view(0)
        return ok(meta=S.run.meta, rows=v["rows"], summary=v["summary"])
    d = storage.find_run(rid)
    if not d:
        return fail("run not found", 404)
    meta, rows, _ = storage.load_run(d)
    p = meta.get("params", {})
    return ok(meta=meta, rows=rows, summary=stats.summarise(rows, float(p.get("lever_r_mm") or 0),
                                                            int(p.get("skip_first") or 0)))


# ------------------------------------------------------------------ hold & PID test (hold.py)
@app.route("/api/hold/start", methods=["POST"])
def hold_start():
    if not S.servo_info or S.running():
        return fail("not connected / a run or hold is already active")
    if (np := no_power()):
        return np
    b, run = body(), S.settings["run"]
    mode = b.get("mode", "legacy")
    if mode not in ("legacy", "extension"):
        return fail("mode = legacy | extension")
    if mode == "extension" and S.servo_info.get("firmware_family") != "robonine":
        return fail("RoboNine controller needs the RoboNine v9 firmware (servo reports %s)" % S.servo_info["firmware_label"])
    try:
        target, step = int(b["target"]), abs(int(b.get("step") or 0))
        per_side, duration = float(b.get("step_hold_s") or 2), float(b.get("duration_s") or 0)
        speed, accel = int(b.get("speed") or run["speed"]), int(b.get("accel") or run["accel"])
    except (KeyError, TypeError, ValueError):
        return fail("target, step and times must be numbers")
    lo, hi = S.settings["soft_min"], S.settings["soft_max"]
    if not lo <= target <= hi:
        return fail("target %d is outside soft limits %d..%d" % (target, lo, hi))
    if step and not (lo <= target - step and target + step <= hi):
        return fail("steps of +/-%d around %d leave the soft limits %d..%d" % (step, target, lo, hi))
    if per_side < 0.5 or duration < 0 or speed < 1 or accel < 100:
        return fail("time per side >= 0.5 s, duration >= 0, speed >= 1, accel >= 100")
    p = dict(mode=mode, id=S.settings["id"], target=target, step=step, step_hold_s=per_side, duration_s=duration,
             speed=speed, accel=accel, acc_reg=max(1, min(254, round(accel / 100))),
             max_temp_c=int(run.get("max_temp_c") or 65), note=b.get("note") or "")
    S.hold = hold.HoldTest(S, p)
    S.hold.thread.start()
    return ok(run_id=S.hold.run_id)


@app.route("/api/hold/stop", methods=["POST"])
def hold_stop():
    if S.hold and S.hold.alive():
        S.hold.stop_ev.set()
    return ok()


@app.route("/api/hold/goal", methods=["POST"])
def hold_goal():
    """Move the hold target while holding (jog / one-off step). Stays inside the soft limits."""
    if not (S.hold and S.hold.alive() and S.hold.state == "running"):
        return fail("no hold is running")
    try:
        S.hold.request_goal(int(body()["target"]))
    except (KeyError, TypeError, ValueError) as e:
        return fail(str(e) or "target must be a number")
    return ok()


# ------------------------------------------------------------------ T3 backlash push test (backlash.py)
@app.route("/api/bl/start", methods=["POST"])
def bl_start():
    if not S.servo_info or S.running():
        return fail("not connected / a run, hold or backlash test is already active")
    if (np := no_power()):
        return np
    b, run = body(), S.settings["run"]
    mode = b.get("mode", "legacy")
    if mode not in ("legacy", "extension"):
        return fail("mode = legacy | extension")
    if mode == "extension" and S.servo_info.get("firmware_family") != "robonine":
        return fail("RoboNine controller needs the RoboNine v9 firmware (servo reports %s)" % S.servo_info["firmware_label"])
    try:
        positions = [int(x) for x in b.get("positions") or []]
        repeats, lever = int(b.get("repeats") or 5), float(b.get("lever_r_mm") or 0)
        speed, accel = int(b.get("speed") or run["speed"]), int(b.get("accel") or run["accel"])
    except (TypeError, ValueError):
        return fail("positions, repeats and lever radius must be numbers")
    lo, hi = S.settings["soft_min"], S.settings["soft_max"]
    if not positions or len(positions) > 6:
        return fail("give 1 to 6 positions")
    for x in positions:
        if not lo <= x <= hi:
            return fail("position %d is outside soft limits %d..%d" % (x, lo, hi))
    if not 1 <= repeats <= 20:
        return fail("repeats 1..20")
    if lever <= 0:
        return fail("lever radius (axis to gauge contact point, mm) is needed to convert mm to degrees")
    reader = b.get("reader", "qwen")
    if reader == "qwen" and S.camera.cap is None:
        return fail("open the camera first (step 2), or choose typed gauge readings")
    p = dict(mode=mode, id=S.settings["id"], positions=positions, repeats=repeats, lever_r_mm=lever,
             with_rest=bool(b.get("with_rest", True)), load_note=(b.get("load_note") or "")[:300], reader=reader,
             speed=speed, accel=accel, acc_reg=max(1, min(254, round(accel / 100))), avg_s=0.6, settle_s=1.5,
             max_temp_c=int(run.get("max_temp_c") or 65), gauge_decimals=run.get("gauge_decimals"))
    S.backlash = backlash.BacklashTest(S, p)
    S.backlash.start()
    return ok(run_id=S.backlash.run_id)


@app.route("/api/bl/record", methods=["POST"])
def bl_record():
    t = S.backlash
    if not (t and t.alive() and t.state == "waiting"):
        return fail("not waiting for a record")
    val = body().get("value")
    try:
        val = None if val in (None, "") else float(val)
    except (TypeError, ValueError):
        return fail("gauge value must be a number")
    t.command("record", val)
    return ok()


@app.route("/api/bl/undo", methods=["POST"])
def bl_undo():
    t = S.backlash
    if not (t and t.alive() and t.state == "waiting" and t.k > 0):
        return fail("nothing to redo")
    t.command("undo")
    return ok()


@app.route("/api/bl/stop", methods=["POST"])
def bl_stop():
    if S.backlash and S.backlash.alive():
        S.backlash.stop_ev.set()
    return ok()


@app.route("/runs/<rid>/frames/<fn>")
def frame(rid, fn):
    if S.backlash and S.backlash.run_id == rid and not S.backlash.final_dir:
        d = S.backlash.dir                                   # live picture thumbnails while the T3 test runs
    else:
        d = S.run.dir if (S.run and S.run.run_id == rid) else storage.find_run(rid)
    if not d:
        return fail("run not found", 404)
    return send_from_directory(os.path.join(d, "frames"), fn)


# ------------------------------------------------------------------ firmware flashing (RoboNine tools/flash)
def fw_releases():
    out = {}
    for which, rel in FIRMWARES.items():
        path = os.path.join(FLASH_DIR, rel)
        m = storage.read_json(path) if os.path.exists(path) else None
        out[which] = dict(present=bool(m), release=(m or {}).get("release"), manifest=path,
                          version=(m or {}).get("extension") or (m or {}).get("factory"),
                          model_bytes=(m or {}).get("model_bytes"))
    return out


@app.route("/api/fw/status")
def fw_status():
    return ok(releases=fw_releases(), job=S.fw_job, flash_dir=FLASH_DIR)


@app.route("/api/fw/download", methods=["POST"])
def fw_download():
    """Fetch RoboNine's published custom v9 release ZIP and keep only release.json + the encoded image."""
    import io
    import urllib.request
    import zipfile
    if S.flashing():
        return fail("a flash is running")
    try:
        with urllib.request.urlopen(ROBONINE_ZIP, timeout=60) as r:
            data = r.read(2_000_000)
        z = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(z.read("release.json"))
        image = manifest.get("image", "")
        if not image or os.path.basename(image) != image or image not in z.namelist():
            return fail("unexpected ZIP contents: %s" % z.namelist())
        dest = os.path.join(FLASH_DIR, "releases", "custom")
        os.makedirs(dest, exist_ok=True)
        for name in ("release.json", image):
            with open(os.path.join(dest, name), "wb") as f:
                f.write(z.read(name))
    except Exception as e:
        return fail("download failed: %s" % e)
    return ok(releases=fw_releases())


def _fw_log(line):
    S.fw_job["log"].append(line.rstrip())
    del S.fw_job["log"][:-400]


def _fw_run(cmd):
    import subprocess
    _fw_log("> python " + " ".join(cmd[2:]))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(cmd, cwd=FLASH_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", creationflags=flags)
    S.fw_proc = p
    for line in p.stdout:
        _fw_log(line)
    return p.wait()


def _fw_worker(which, port, baud, sid):
    import sys
    job = S.fw_job
    py = [sys.executable, "-u"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(FLASH_DIR, "..", "..", "backups", "settings", "servo-%d-%s-before-%s.json" % (sid, stamp, which))
    try:
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        _fw_log("1/3 backing up servo settings")
        code = _fw_run(py + ["settings.py", "backup", "--port", port, "--id", str(sid), "--baud", str(baud),
                             "--file", os.path.normpath(backup)])
        if code != 0:
            raise RuntimeError("settings backup failed (exit %d); nothing was flashed" % code)
        _fw_log("2/3 flashing %s. Do not unplug power or close the window." % which)
        code = _fw_run(py + ["flash.py", "flash", "--manifest", FIRMWARES[which], "--port", port, "--id", str(sid),
                             "--baud", str(baud), "--yes"])
        job["code"] = code
        if code == 2:
            job["note"] = ("Factory firmware is running but the servo settings changed. Restore them: "
                           "python tools\\flash\\settings.py restore --port %s --id %d --file \"%s\""
                           % (port, sid, os.path.normpath(backup)))
        elif code != 0:
            raise RuntimeError("flash.py exit %d. If the servo no longer answers, see tools/flash/README.md "
                               "'Recover' (servo alone on the bus)." % code)
        _fw_log("3/3 reconnecting")
        time.sleep(1.0)
        S.bus = Bus(port, baud)
        S.servo_info = S.bus.identify(sid)
        S.servo_info["id"] = sid
        job["firmware_after"] = S.servo_info.get("firmware_label")
        _fw_log("servo now reports: %s" % job["firmware_after"])
        job["state"] = "done"
    except Exception as e:
        job["state"], job["error"] = "failed", str(e)
        _fw_log("FAILED: %s" % e)
    finally:
        # the position offset may have changed: centre and positions must be redone on the new firmware
        S.calib.update(center_ok=False, positions_saved=False,
                       firmware_changed=time.strftime("%Y-%m-%d %H:%M:%S") + " -> " + which)
        S.settings["calib"] = S.calib
        S.save_settings()
        job["finished"] = time.strftime("%H:%M:%S")


@app.route("/api/fw/flash", methods=["POST"])
def fw_flash():
    b = body()
    which = b.get("which")
    if which not in FIRMWARES:
        return fail("choose robonine or feetech")
    if (b.get("confirm") or "").strip() != "FLASH":
        return fail("type FLASH to confirm")
    if S.running():
        return fail("stop the run first")
    if not fw_releases()[which]["present"]:
        return fail("firmware file missing: download it first")
    if not S.servo_info:
        return fail("connect the servo first (step 1)")
    if (np := no_power()):
        return np
    sid, port, baud = S.settings["id"], S.settings["port"], S.settings["baud"]
    try:                                     # flash.py requires: torque off, settings locked, no alarm
        S.bus.torque(sid, False)
        S.bus.write_u8(sid, R_LOCK, 1)
    except BusError as e:
        return fail("could not prepare the servo: %s" % e)
    S.runner_owns_bus = True
    S.bus.close()
    S.bus, S.servo_info, S.telem = None, None, None
    S.runner_owns_bus = False
    S.fw_job = dict(which=which, state="running", log=[], code=None, started=time.strftime("%H:%M:%S"),
                    firmware_before=b.get("firmware_before"))
    threading.Thread(target=_fw_worker, args=(which, port, baud, sid), name="flash", daemon=True).start()
    return ok()


# ------------------------------------------------------------------ settings restore (wiped settings area)
BACKUP_DIR = os.path.normpath(os.path.join(FLASH_DIR, "..", "..", "backups"))


def settings_backups():
    """Healthy register snapshots: settings.py backups (incl. the one taken before every flash from the bench)
    and the 'before' snapshot of each normal flash report. Newest first."""
    import glob
    from sts import validate_settings_table
    out = []
    cands = [(p, "registers_hex") for p in glob.glob(os.path.join(BACKUP_DIR, "settings", "*.json"))]
    cands += [(p, "before_registers_hex") for p in glob.glob(os.path.join(BACKUP_DIR, "flash", "*", "report.json"))]
    for path, key in cands:
        d = storage.read_json(path) or {}
        if key == "before_registers_hex" and d.get("mode") != "flash":
            continue
        try:
            t = validate_settings_table(bytes.fromhex(d.get(key) or ""))
        except ValueError:
            continue
        ts = d.get("created_unix_s") or d.get("started_unix_s") or os.path.getmtime(path)
        out.append(dict(file=os.path.relpath(path, BACKUP_DIR), when=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                        ts=ts, id=t[5], fw="%d.%d" % (t[0], t[1]), model="%d.%d" % (t[3], t[4]),
                        limits="%d..%d" % (t[9] | t[10] << 8, t[11] | t[12] << 8),
                        volts="%.1f..%.1f" % (t[15] / 10, t[14] / 10), offset_raw=t[31] | t[32] << 8,
                        source="flash report" if key == "before_registers_hex" else "settings backup"))
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


@app.route("/api/settings/backups")
def settings_backups_api():
    return ok(backups=settings_backups(), backup_dir=BACKUP_DIR)


@app.route("/api/settings/restore", methods=["POST"])
def settings_restore():
    """Write a backed-up settings snapshot back into the servo. Deliberately NOT behind no_power(): a wiped
    settings area causes the voltage alarm this fixes."""
    b = body()
    if (b.get("confirm") or "").strip() != "RESTORE":
        return fail("type RESTORE to confirm")
    if S.running():
        return fail("stop the run / upload first")
    if not S.bus:
        return fail("connect first (step 1). A servo with wiped settings still connects at ID 1.")
    known = {r["file"]: r for r in settings_backups()}
    if b.get("file") not in known:
        return fail("choose one of the listed backups")
    path = os.path.join(BACKUP_DIR, b["file"])
    d = storage.read_json(path) or {}
    table = bytes.fromhex(d.get("registers_hex") or d.get("before_registers_hex"))
    sid = S.settings["id"]
    S.runner_owns_bus = True
    try:
        res = S.bus.restore_settings(sid, table)
        S.servo_info = S.bus.identify(sid)
        S.servo_info["id"] = sid
    except (BusError, ValueError) as e:
        return fail("restore failed: %s" % e)
    finally:
        S.runner_owns_bus = False
    a = res["after"]
    S.settings["soft_min"], S.settings["soft_max"] = S.servo_info["min_limit"], S.servo_info["max_limit"]
    S.calib.update(center_ok=False, positions_saved=False,
                   settings_restored=time.strftime("%Y-%m-%d %H:%M:%S") + " from " + b["file"])
    S.settings["calib"] = S.calib
    S.save_settings()
    return ok(written=res["written"], mismatches=res["mismatches"], firmware=S.servo_info["firmware_label"],
              voltage=a[62] / 10, alarm=a[65], model="%d.%d" % (a[3], a[4]))


def shutdown():
    """Window closed: stop a run (files are finished and moved), release torque state as is, close port + camera."""
    if S.flashing() and getattr(S, "fw_proc", None):
        S.fw_proc.wait(timeout=180)          # never cut a firmware transfer short
    if S.run and S.run.thread.is_alive():
        S.run.stop_ev.set()
        S.run.thread.join(timeout=60)
    S.camera.close()
    if S.bus:
        S.bus.close()


@app.route("/api/quit", methods=["POST"])
def quit_app():
    """A new start of the bench asks an idle older copy to close (never during a run or a flash)."""
    if S.running():
        return fail("a run or firmware upload is active")
    def bye():
        time.sleep(0.3)
        shutdown()
        os._exit(0)
    threading.Thread(target=bye, daemon=True).start()
    return ok()


def port_busy():
    import socket
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", 5077)) == 0


def take_over(url):
    """Another copy owns the port (Windows lets two copies share it, which mixes up their answers).
    Idle -> ask it to close and start fresh. Busy (run / flash) -> return 'attach': only open a window on it."""
    import urllib.request
    try:
        with urllib.request.urlopen(url + "/api/state", timeout=3) as r:
            st = json.loads(r.read())
    except Exception:
        st = {}
    if st.get("running"):
        return "attach"
    try:
        req = urllib.request.Request(url + "/api/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass
    for _ in range(40):
        if not port_busy():
            return "fresh"
        time.sleep(0.25)
    return "stuck"


def main():
    import sys
    url = "http://127.0.0.1:5077"
    if port_busy():
        how = take_over(url)
        if how == "stuck":
            print("An older bench copy (without the close command) still holds port 5077.\n"
                  "Close it in Task Manager (python, 'app.py'), then start again.")
            sys.exit(1)
        if how == "attach":                  # a test or upload is running in the other copy: just show it
            try:
                import webview
                webview.create_window("STS3215 Repeatability Bench (running test)", url, width=1500, height=950)
                webview.start()
            except ImportError:
                import webbrowser
                webbrowser.open(url)
            return
    try:
        S.camera.open(S.settings["camera"])
    except Exception:
        pass
    if "--browser" in sys.argv:
        print("STS3215 Repeatability Bench -> " + url)
        app.run(host="127.0.0.1", port=5077, threaded=True, debug=False)
        return
    threading.Thread(target=lambda: app.run(host="127.0.0.1", port=5077, threaded=True, debug=False),
                     name="http", daemon=True).start()
    try:
        import webview
    except ImportError:                          # fallback: Edge app window (no tabs / address bar)
        import subprocess
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        if os.path.exists(edge):
            subprocess.Popen([edge, "--app=" + url, "--window-size=1500,950",
                              "--user-data-dir=" + os.path.join(storage.STAGING, "..", "edge-profile")])
        else:
            import webbrowser
            webbrowser.open(url)
        print("Bench running at %s -- close this console to stop." % url)
        threading.Event().wait()
        return
    win = webview.create_window("STS3215 Repeatability Bench  v" + APP_VERSION, url, width=1500, height=950, min_size=(1000, 700),
                                confirm_close=True)
    win.events.closed += shutdown
    webview.start()
    shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()
