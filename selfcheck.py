"""Offline self-check: no COM port, no camera, no Qwen. Simulated servo speaks legacy STS and RoboNine extension v9.

    python selfcheck.py
"""
import os
import shutil
import struct
import sys
import tempfile
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sts  # noqa: E402
import stats  # noqa: E402
import storage  # noqa: E402

# SAFETY: redirect every output folder to a temp dir before anything runs (never write the real data/ or docs/)
TMP = tempfile.mkdtemp(prefix="sts-bench-selfcheck-")
storage.STAGING = os.path.join(TMP, "staging")
storage.DATA_ROOT = os.path.join(TMP, "data")
storage.REPORT_DIR = os.path.join(TMP, "docs", "test_reports")
storage.OLD_RUNS = os.path.join(TMP, "old")
storage.PROJECT = TMP
import vision  # noqa: E402


class FakeServo:
    """Very small STS3215 model: position slews toward goal at `speed`; backlash-like random end error."""

    def __init__(self, sid=1, robonine=True):
        self.sid, self.robonine = sid, robonine
        self.regs = bytearray(256)
        self.regs[0], self.regs[1], self.regs[2], self.regs[3] = 3, 10, 3, 9
        struct.pack_into("<HH", self.regs, 0x09, 0, 4095)
        self.regs[0x3E], self.regs[0x3F] = 120, 31
        self.pos, self.goal, self.speed = 2000.0, 2000.0, 1000.0
        self.t = time.monotonic()
        self.cfg = None
        self.mode, self.session_prep, self.session, self.seq = 0, 0, 0, 0
        self.rng = np.random.default_rng(1)
        self.out = bytearray()

    def _step(self):
        now = time.monotonic()
        dt, self.t = now - self.t, now
        d = self.goal - self.pos
        step = self.speed * dt
        if abs(d) <= step:
            if self.pos != self.goal:
                self.pos = self.goal + (self.rng.normal(0, 0.6) if self.mode == 0 else self.rng.normal(0, 0.2))
                self.goal = self.pos
        else:
            self.pos += step if d > 0 else -step

    def handle(self, pkt):
        self._step()
        sid, ln, ins = pkt[2], pkt[3], pkt[4]
        par = pkt[5:5 + ln - 2]
        assert sts.checksum(pkt[2:-1]) == pkt[-1], "bad checksum from host"
        if sid != self.sid:
            return
        moving = abs(self.goal - self.pos) > 0.5
        p = int(round(self.pos))
        struct.pack_into("<H", self.regs, 0x38, sts.sign15_encode(p))
        self.regs[0x42] = 1 if moving else 0
        if ins == sts.PING:
            return self.reply(0, b"")
        if ins == sts.READ:
            a, n = par[0], par[1]
            return self.reply(0, bytes(self.regs[a:a + n]))
        if ins == sts.WRITE:
            a, data = par[0], bytes(par[1:])
            self.regs[a:a + len(data)] = data
            if a == sts.R_TORQUE and data[0] == 1 and self.mode == 1 and self.session_prep:
                self.session, self.seq, self.goal = self.session_prep, 0, self.pos
            if a == sts.R_TORQUE and data[0] == 0:
                self.session = 0
            if a == sts.R_TORQUE and data[0] == 128:          # calibrate: current position reads 2048
                corr = int(round(self.pos)) - 2048
                struct.pack_into("<H", self.regs, sts.R_POS_CORRECTION, abs(corr) | (0x800 if corr < 0 else 0))
                self.pos = self.goal = 2048.0
                self.regs[sts.R_TORQUE] = 0
            if a <= sts.R_GOAL_POS < a + len(data):
                off = sts.R_GOAL_POS - a
                self.goal = sts.sign15_decode(data[off] | data[off + 1] << 8)
                if a == sts.R_ACC and len(data) >= 7:
                    self.speed = max(1, data[5] | data[6] << 8)
                self.mode = 0
            return self.reply(0, b"")
        if not self.robonine:
            return self.reply(0x08, b"")        # factory: instruction error, no extension
        if ins == sts.EXT_READ:
            sel, n = par[0], par[1]
            if sel == sts.SEL_CAPS:
                return self.reply(0, bytes([0]) + b"ARMC" + bytes([9, 0, 0, 1]))
            if sel == sts.SEL_CONFIG:
                return self.reply(0, bytes([0]) + (self.cfg or bytes(24)))
            if sel == sts.SEL_FEEDBACK:
                flags = 0
                if self.session:
                    flags = sts.FLAG_FRESH | sts.FLAG_ACTIVE | sts.FLAG_DRIVE | sts.FLAG_READY | sts.FLAG_SELECTED
                    if moving:
                        flags |= sts.FLAG_MOTION
                fb = struct.pack(sts.FEEDBACK_FMT, 1, 1, self.session, self.seq, 0, flags, int(self.pos * 256), 0,
                                 0, 0, 120, 31, 0, 0, 0)
                return self.reply(0, bytes([0]) + fb)
        if ins == sts.EXT_WRITE:
            sel, block = par[0], bytes(par[1:])
            if sel == sts.SEL_CONFIG:
                self.cfg = block
            elif sel == sts.SEL_SESSION:
                self.mode, sess = struct.unpack("<BH", block)
                self.session_prep = sess
            return self.reply(0, bytes([0]))
        if ins == sts.EXT_TARGETS:
            sess = struct.unpack_from("<H", par, 0)[0]
            _id, seq, pos_q8, spd = struct.unpack_from("<BHiH", par, 2)
            if sess != self.session or seq <= self.seq:
                return self.reply(0, bytes([2]))
            self.seq, self.goal, self.speed = seq, pos_q8 / 256.0, spd
            return self.reply(0, bytes([0]))
        return self.reply(0, bytes([5]))

    def reply(self, err, params):
        body = bytes([self.sid, len(params) + 2, err]) + params
        self.out += b"\xff\xff" + body + bytes([sts.checksum(body)])


class FakeSerial:
    def __init__(self, servo):
        self.s = servo

    @property
    def in_waiting(self):
        return len(self.s.out)

    def reset_input_buffer(self):
        self.s.out.clear()

    def write(self, b):
        self.s.handle(bytes(b))

    def read(self, n):
        d = bytes(self.s.out[:n])
        del self.s.out[:n]
        if not d:
            time.sleep(0.001)
        return d

    def close(self):
        pass


class FakeCamera:
    cap = True

    def fresh_frame(self, after_t, timeout=1.0):
        return np.full((120, 200, 3), 200, np.uint8), time.time()

    def latest(self):
        return self.fresh_frame(0)


class FakeReader:
    def __init__(self):
        self.rng = np.random.default_rng(2)

    def describe(self):
        return "fake"

    def read(self, jpg, decimals=None):
        time.sleep(0.01)
        v = round(1.20 + self.rng.normal(0, 0.004), 3)
        return dict(status="ok", value=v, raw_text="%.2f" % v, confidence="high", unreadable=False, notes="", ms=10)


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        check.failed += 1


check.failed = 0


def run_case(robonine, mode, pattern):
    import runner
    servo = FakeServo(robonine=robonine)
    bus = sts.Bus("FAKE", ser=FakeSerial(servo))
    info = bus.identify(1)
    tmp = TMP

    class A:
        pass
    a = A()
    a.bus, a.servo_info, a.camera, a.reader, a.data_dir = bus, info, FakeCamera(), FakeReader(), tmp
    a.settings = dict(port="FAKE", camera=0, roi={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5},
                      ext_config=dict(sts.DEFAULT_EXT_CONFIG))
    a.runner_owns_bus = False
    p = dict(id=1, mode=mode, pattern=pattern, direction="+", target=2048, offset=40, cycles=4, speed=3000,
             accel=5000, acc_reg=50, dwell_s=0.05, tol=3, move_timeout_s=3, reader="qwen", lever_r_mm=50.0,
             skip_first=0, max_temp_c=65, note="selfcheck")
    r = runner.Run(a, p)
    r.start()
    r.thread.join(30)
    v = r.view()
    tag = "%s/%s/%s" % ("robonine" if robonine else "feetech", mode, pattern)
    exp = 4 * (2 if pattern == "bidirectional" else 1)
    check("%s: run finished '%s' (%s)" % (tag, v["state"], v["message"]), v["state"] == "done")
    check("%s: %d samples (expected %d)" % (tag, v["n_rows"], exp), v["n_rows"] == exp)
    check("%s: all arrived ok" % tag, all(rw["arrive"] == "ok" for rw in r.rows))
    check("%s: Qwen filled every row" % tag, all(rw["qwen_status"] == "ok" for rw in r.rows))
    check("%s: moved out of staging into data/%s/" % (tag, r.test_id),
          r.final_dir == os.path.join(storage.DATA_ROOT, r.test_id, r.run_id) and not os.path.exists(storage.staging_dir(r.run_id)))
    check("%s: files written" % tag, all(os.path.exists(os.path.join(r.dir, f)) for f in
                                         ("samples.csv", "telemetry.csv", "meta.json", "summary.json", "run.xlsx")))
    check("%s: frames saved" % tag, len(os.listdir(os.path.join(r.dir, "frames"))) == exp)
    check("%s: report in docs/test_reports" % tag, os.path.exists(os.path.join(storage.REPORT_DIR, r.run_id + ".md")))
    from openpyxl import load_workbook
    wb = load_workbook(os.path.join(r.dir, "run.xlsx"), read_only=True)
    tel = list(wb["Telemetry"].iter_rows(values_only=True))
    check("%s: workbook sheets %s" % (tag, wb.sheetnames), wb.sheetnames == ["Summary", "Samples", "Telemetry", "Parameters", "Columns"])
    check("%s: telemetry has %d rows with temperature + voltage" % (tag, len(tel) - 1),
          len(tel) > exp * 3 and all(t[11] is not None and t[12] is not None for t in tel[1:]))
    wb.close()
    if mode == "extension":
        check("%s: servo back in legacy mode after run" % tag, servo.mode == 0 and servo.session == 0)
    return info


def main():
    import camera
    c = camera.Camera()
    img = np.zeros((4, 6, 3), np.uint8)
    img[0, 0] = 255
    c.rotate = 180
    check("camera rotate 180 moves top-left pixel to bottom-right", c.orient(img)[3, 5, 0] == 255)
    c.rotate, c.flip = 90, "none"
    check("camera rotate 90 swaps width/height", c.orient(img).shape[:2] == (6, 4))
    # protocol framing: the doc's example packet
    check("capabilities read packet == doc example FF FF 01 04 90 90 08 D2",
          sts.packet(1, 0x90, bytes([0x90, 0x08])) == bytes.fromhex("FFFF0104909008D2"))
    check("sign15 round trip", all(sts.sign15_decode(sts.sign15_encode(v)) == v for v in (-4095, -1, 0, 1, 4095)))
    check("config block is 24 bytes", len(struct.pack(sts.CONFIG_FMT, *[sts.DEFAULT_EXT_CONFIG[k]
                                                                      for k in sts.CONFIG_FIELDS])) == 24)
    # vision parsing
    r = vision.parse_reply('```json\n{"raw_text":"-0.45","value":-0.45,"decimal_places":2,"confidence":"high",'
                           '"unreadable":false,"notes":""}\n```')
    check("vision parse -0.45", r["value"] == -0.45 and not r["unreadable"])
    r = vision.parse_reply('thinking... {"raw_text":"OFF","value":null,"decimal_places":0,"confidence":"low",'
                           '"unreadable":true,"notes":"x"}')
    check("vision parse unreadable", r["value"] is None and r["unreadable"])
    check("vision parse keeps 3 decimals", vision.parse_value("1.234") == 1.234)
    check("prompt has 7-seg rules", "seven_segment_rules" in vision.load_prompt())
    # stats
    rows = [dict(cycle=i, dir="+", qwen_value=1.0 + 0.01 * (i % 2), err_counts=0) for i in range(1, 11)]
    rows += [dict(cycle=i, dir="-", qwen_value=1.05, err_counts=1) for i in range(1, 11)]
    rows[0]["manual_value"] = 1.005
    s = stats.summarise(rows, 50.0)
    check("stats n per direction", s["groups"]["+"]["gauge_mm"]["n"] == 10)
    check("stats manual override used", abs(s["groups"]["+"]["gauge_mm"]["min"] - 1.0) < 1e-9 and
          abs(s["groups"]["+"]["gauge_mm"]["mean"] - 1.0045) < 1e-9)
    check("stats lost motion", abs(s["lost_motion_mm"] - (1.0045 - 1.05)) < 1e-9)
    check("stats arcsec = mm/r", abs(s["groups"]["+"]["gauge_arcsec"]["sd"] -
                                     s["groups"]["+"]["gauge_mm"]["sd"] / 50 * stats.ARCSEC_PER_RAD) < 1e-6)
    # identification + full runs on the simulated servo
    info = run_case(False, "legacy", "same")
    check("factory firmware detected as feetech (%s)" % info["firmware_label"], info["firmware_family"] == "feetech")
    info = run_case(True, "legacy", "bidirectional")
    check("RoboNine detected (%s)" % info["firmware_label"], info["firmware_family"] == "robonine")
    run_case(True, "extension", "same")
    run_case(True, "extension", "bidirectional")
    # calibration on the simulated servo
    servo = FakeServo()
    servo.pos = servo.goal = 1500.0
    bus = sts.Bus("FAKE", ser=FakeSerial(servo))
    pos, corr = bus.calibrate_center(1)
    check("centre: reads 2048 after calibrate (offset %s)" % corr, pos == 2048)
    check("limits written + read back", bus.write_limits(1, 1900, 2300) == (1900, 2300) and servo.regs[sts.R_LOCK] == 1)
    # trial run: 3 cycles, '_trial' in the name
    import runner
    info = bus.identify(1)

    class A:
        pass
    a = A()
    a.bus, a.servo_info, a.camera, a.reader = bus, info, FakeCamera(), FakeReader()
    a.settings = dict(port="FAKE", camera=0, roi=None, ext_config=dict(sts.DEFAULT_EXT_CONFIG))
    a.runner_owns_bus = False
    p = dict(id=1, mode="legacy", pattern="same", direction="+", target=2100, offset=40, cycles=3, planned_cycles=100,
             trial=True, speed=3000, accel=5000, acc_reg=50, dwell_s=0.05, tol=3, move_timeout_s=3, reader="qwen",
             lever_r_mm=50.0, skip_first=0, max_temp_c=65, note="selfcheck", calib=dict(center_pos=2048))
    r = runner.Run(a, p)
    r.start()
    r.thread.join(30)
    check("trial run: 3 samples, '_trial' run id, report says trial", r.view()["n_rows"] == 3 and r.run_id.endswith("_trial")
          and "Trial | yes" in open(os.path.join(storage.REPORT_DIR, r.run_id + ".md"), encoding="utf-8").read())
    check("runs index lists every run", len(storage.read_csv(os.path.join(storage.DATA_ROOT, "runs-index.csv"))) == 1 + 2 + 1 + 2 + 1)
    check("docs/test_reports/README.md written", os.path.exists(os.path.join(storage.REPORT_DIR, "README.md")))
    os.makedirs(os.path.join(storage.STAGING, "crashed"), exist_ok=True)
    storage.write_json(os.path.join(storage.STAGING, "crashed", "meta.json"),
                       dict(run_id="crashed", test_id="T4.1a", status="running", params={}))
    moved = storage.recover()
    check("crashed staging run recovered as interrupted", len(moved) == 1 and
          storage.read_json(os.path.join(moved[0], "meta.json"))["status"] == "interrupted")
    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%s" % ("ALL PASSED" if not check.failed else "%d FAILED" % check.failed))
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
