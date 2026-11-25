"""STS3215 bus: legacy STS protocol (factory Feetech 3.10 AND RoboNine legacy mode) + RoboNine extension protocol v9.0.

Sources: RoboNine STS32XX-Firmware DETAILED_DOCUMENTATION.md (extension v9.0) and host/servo-driver/registers/ControlTable.js
(legacy register map). Everything that touches the port goes through Bus.lock, so the telemetry poller and the test runner
can share one adapter.
"""
import struct
import threading
import time

import serial

# ---- legacy instructions / registers --------------------------------------------------------------------------------
PING, READ, WRITE = 0x01, 0x02, 0x03
EXT_READ, EXT_WRITE, EXT_GRAVITY, EXT_TARGETS = 0x90, 0x91, 0x99, 0x9A
SEL_CAPS, SEL_CONFIG, SEL_SESSION, SEL_FEEDBACK, SEL_TUNING = 0x90, 0xA0, 0xB8, 0xC1, 0xC2

R_FW_MAJOR, R_FW_MINOR, R_MODEL_MINOR, R_MODEL_MAJOR = 0x00, 0x01, 0x02, 0x03
R_MIN_LIMIT, R_MAX_LIMIT = 0x09, 0x0B
R_TORQUE, R_ACC, R_GOAL_POS = 0x28, 0x29, 0x2A
R_POS_CORRECTION, R_LOCK = 0x1F, 0x37
R_PRESENT_POS = 0x38           # 0x38..0x46 read as one block (15 bytes) for telemetry
R_MOVING = 0x42

CURRENT_MA_PER_LSB = 6.5        # STS3215 present current unit


def checksum(body):
    return (~sum(body)) & 0xFF


def packet(sid, instr, params=b""):
    body = bytes([sid, len(params) + 2, instr]) + bytes(params)
    return b"\xff\xff" + body + bytes([checksum(body)])


def sign15_decode(v):
    return -(v & 0x7FFF) if v & 0x8000 else v


def sign15_encode(v):
    v = int(round(v))
    return (abs(v) & 0x7FFF) | (0x8000 if v < 0 else 0)


def sign11_decode(v):
    """Position correction (0x1F), same as RoboNine RegisterCodec.readPositionOffset (display only)."""
    if v < 0x0800:
        return v
    if v == 0x0800:
        return 0
    if v < 0x1000:
        return 0x0800 - v
    if v < 0x1800:
        return v - 0x0800
    if v < 0x2000:
        return 0x1000 - v
    return 0


def sign10_decode(v):
    return -(v & 0x3FF) if v & 0x400 else (v & 0x3FF)


class BusError(RuntimeError):
    pass


# ---- extension structures ------------------------------------------------------------------------------------------
CONFIG_FMT = "<HHHHHHHBBHHHH"   # 24 bytes, DETAILED_DOCUMENTATION 'Configuration - 0xA0'
CONFIG_FIELDS = ["kp_q8", "kd_q8", "ki_q8", "integral_limit", "effort_limit", "velocity_limit", "acceleration_limit",
                 "velocity_filter_q8", "ramp_time_ms", "ff_pos_gain_q8", "ff_neg_gain_q8", "ff_effort_limit",
                 "gravity_slew"]
FEEDBACK_FMT = "<IHHHHHiihhBBBBH"  # 32 bytes, 'Operational feedback - 0xC1'
assert struct.calcsize(CONFIG_FMT) == 24 and struct.calcsize(FEEDBACK_FMT) == 32

FLAG_FRESH, FLAG_ACTIVE, FLAG_LIMITED, FLAG_DRIVE, FLAG_STARTED, FLAG_READY, FLAG_MOTION, FLAG_SELECTED = (
    1 << 0, 1 << 1, 1 << 3, 1 << 4, 1 << 5, 1 << 7, 1 << 12, 1 << 14)

# Default single-joint profile: RoboNine host/arm-control/profiles/so101/motor-3.json (elbow), velocity limit raised
# so the UI speed is not capped. Edit in the UI; it is stored with every run.
DEFAULT_EXT_CONFIG = dict(kp_q8=2560, kd_q8=32, ki_q8=4096, integral_limit=64, effort_limit=1000, velocity_limit=3000,
                          acceleration_limit=10000, velocity_filter_q8=128, ramp_time_ms=100, ff_pos_gain_q8=74,
                          ff_neg_gain_q8=80, ff_effort_limit=1000, gravity_slew=1000)


def decode_feedback(b):
    (t_ms, sample, session, tseq, gseq, flags, pos_q8, vel_q8, out, cur, volt, temp, fault, _r, age) = \
        struct.unpack(FEEDBACK_FMT, bytes(b[:32]))
    return dict(t_ms=t_ms, sample=sample, session=session, target_seq=tseq, gravity_seq=gseq, flags=flags,
                pos=pos_q8 / 256.0, vel=vel_q8 / 256.0, output=out, current_raw=cur, voltage=volt / 10.0,
                temp=temp, fault=fault, age_ms=age,
                fresh=bool(flags & FLAG_FRESH), active=bool(flags & FLAG_ACTIVE), drive=bool(flags & FLAG_DRIVE),
                ready=bool(flags & FLAG_READY), moving=bool(flags & FLAG_MOTION), limited=bool(flags & FLAG_LIMITED),
                selected=bool(flags & FLAG_SELECTED))


class Bus:
    def __init__(self, port, baud=1000000, timeout=0.005, ser=None):
        self.port_name, self.baud = port, baud
        self.ser = ser or serial.Serial(port, baud, timeout=timeout, write_timeout=0.2)
        self.lock = threading.RLock()
        self.errors = 0

    def close(self):
        with self.lock:
            try:
                self.ser.close()
            except Exception:
                pass

    # ---- raw transaction ----
    def _read_reply(self, sid, timeout):
        """-> (error_byte, params bytes). Hunts for FF FF <sid>."""
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self.ser.read(max(1, self.ser.in_waiting or 1))
            if chunk:
                buf += chunk
            while True:
                i = buf.find(b"\xff\xff")
                if i < 0:
                    if len(buf) > 1:
                        del buf[:-1]
                    break
                if len(buf) < i + 4:
                    break
                rid, ln = buf[i + 2], buf[i + 3]
                if rid == 0xFF or ln < 2:          # FF FF FF ... : shift one byte
                    del buf[:i + 1]
                    continue
                if len(buf) < i + 4 + ln:
                    break
                frame = bytes(buf[i + 2:i + 4 + ln])   # id, len, err, params..., chk
                del buf[:i + 4 + ln]
                if checksum(frame[:-1]) != frame[-1]:
                    self.errors += 1
                    continue
                if rid != sid:
                    continue
                return frame[2], frame[3:-1]
        raise BusError("no reply from ID %d (timeout %.0f ms)" % (sid, timeout * 1000))

    def transact(self, sid, instr, params=b"", reply=True, timeout=0.05):
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(packet(sid, instr, params))
            if not reply or sid == 0xFE:
                return None, b""
            return self._read_reply(sid, timeout)

    # ---- legacy ----
    def ping(self, sid):
        try:
            self.transact(sid, PING, timeout=0.03)
            return True
        except BusError:
            return False

    def scan(self, ids=range(0, 254)):
        found = []
        for i in ids:
            if self.ping(i):
                found.append(i)
        return found

    def read(self, sid, addr, n):
        err, data = self.transact(sid, READ, bytes([addr, n]))
        if len(data) != n:
            raise BusError("short read %d/%d from ID %d @0x%02X" % (len(data), n, sid, addr))
        return err, data

    def write(self, sid, addr, data):
        err, _ = self.transact(sid, WRITE, bytes([addr]) + bytes(data))
        return err

    def write_u8(self, sid, addr, v):
        return self.write(sid, addr, [v & 0xFF])

    def identify(self, sid):
        _, d = self.read(sid, R_FW_MAJOR, 4)
        _, lim = self.read(sid, R_MIN_LIMIT, 4)
        info = dict(fw="%d.%d" % (d[0], d[1]), model="%d.%d" % (d[3], d[2]),
                    min_limit=lim[0] | lim[1] << 8, max_limit=lim[2] | lim[3] << 8)
        caps = self.ext_caps(sid)
        info["extension"] = caps
        if caps and caps["magic"] == "ARMC":
            info["firmware_family"] = "robonine"
            info["firmware_label"] = "RoboNine v%d.%d (fw %s)" % (caps["major"], caps["minor"], info["fw"])
        else:
            info["firmware_family"] = "feetech"
            info["firmware_label"] = "Feetech factory %s" % info["fw"]
        return info

    def telemetry(self, sid):
        _, d = self.read(sid, R_PRESENT_POS, 15)     # 0x38..0x46
        pos = sign15_decode(d[0] | d[1] << 8)
        spd = sign15_decode(d[2] | d[3] << 8)
        pwm = sign10_decode(d[4] | d[5] << 8)
        cur = sign15_decode(d[13] | d[14] << 8)
        _, t = self.read(sid, R_TORQUE, 1)
        return dict(pos=pos, speed=spd, load=pwm, voltage=d[6] / 10.0, temp=d[7], status=d[9], moving=d[10],
                    current_ma=round(cur * CURRENT_MA_PER_LSB, 1), torque=t[0])

    def torque(self, sid, on):
        return self.write_u8(sid, R_TORQUE, 1 if on else 0)

    def legacy_move(self, sid, pos, speed, acc_reg):
        """One write at 0x29: acceleration, goal position, goal time(0), goal speed."""
        p, s = sign15_encode(pos), int(max(0, min(32766, speed)))
        return self.write(sid, R_ACC, [int(max(0, min(254, acc_reg))), p & 0xFF, p >> 8, 0, 0, s & 0xFF, s >> 8])

    def legacy_hold_here(self, sid):
        """Goal := present position, then torque on (factory firmware would otherwise jump to an old goal)."""
        pos = self.telemetry(sid)["pos"]
        p = sign15_encode(pos)
        self.write(sid, R_GOAL_POS, [p & 0xFF, p >> 8])
        self.torque(sid, True)
        return pos

    # ---- calibration ----
    def calibrate_center(self, sid):
        """Torque off, write 128 to torque-enable 0x28: the servo makes its CURRENT position read 2048 and saves the
        offset (Feetech factory and RoboNine firmware). Returns the position read back."""
        self.torque(sid, False)
        time.sleep(0.05)
        self.write_u8(sid, R_TORQUE, 128)
        time.sleep(0.3)                           # EEPROM write
        _, c = self.read(sid, R_POS_CORRECTION, 2)
        pos = self.telemetry(sid)["pos"]
        return pos, sign11_decode(c[0] | c[1] << 8)

    def write_limits(self, sid, lo, hi):
        """Saved position limits (EEPROM 0x09..0x0C): unlock 0x37, write, relock, read back."""
        if not (0 <= lo < hi <= 4095):
            raise BusError("limits must satisfy 0 <= min < max <= 4095")
        self.torque(sid, False)
        self.write_u8(sid, R_LOCK, 0)
        try:
            self.write(sid, R_MIN_LIMIT, [lo & 0xFF, lo >> 8, hi & 0xFF, hi >> 8])
            time.sleep(0.05)
        finally:
            self.write_u8(sid, R_LOCK, 1)
        _, d = self.read(sid, R_MIN_LIMIT, 4)
        got = (d[0] | d[1] << 8, d[2] | d[3] << 8)
        if got != (lo, hi):
            raise BusError("limits read back %s, expected %s" % (got, (lo, hi)))
        return got

    # ---- extension v9 ----
    def ext_read(self, sid, selector, n, timeout=0.05):
        err, data = self.transact(sid, EXT_READ, bytes([selector, n]), timeout=timeout)
        if not data:
            raise BusError("extension read 0x%02X: empty reply" % selector)
        status, payload = data[0], data[1:]
        if status != 0:
            raise BusError("extension read 0x%02X status %d (legacy err 0x%02X)" % (selector, status, err))
        if len(payload) != n:
            raise BusError("extension read 0x%02X: %d of %d bytes" % (selector, len(payload), n))
        return payload

    def ext_write(self, sid, selector, block):
        err, data = self.transact(sid, EXT_WRITE, bytes([selector]) + bytes(block))
        status = data[0] if data else None
        if status != 0:
            raise BusError("extension write 0x%02X status %s (legacy err 0x%02X)" % (selector, status, err or 0))

    def ext_caps(self, sid):
        try:
            d = self.ext_read(sid, SEL_CAPS, 8, timeout=0.04)
        except BusError:
            return None
        return dict(magic=bytes(d[:4]).decode("ascii", "replace"), major=d[4], minor=d[5], period_ms=d[7])

    def ext_feedback(self, sid):
        return decode_feedback(self.ext_read(sid, SEL_FEEDBACK, 32))

    def ext_write_config(self, sid, cfg):
        vals = [int(cfg[k]) for k in CONFIG_FIELDS]
        block = struct.pack(CONFIG_FMT, *vals)
        self.ext_write(sid, SEL_CONFIG, block)
        back = self.ext_read(sid, SEL_CONFIG, 24)
        if bytes(back) != block:
            raise BusError("extension config read-back differs (rejected field?)")

    def ext_select(self, sid, mode, session):
        self.ext_write(sid, SEL_SESSION, struct.pack("<BH", mode, session))

    def ext_targets(self, sid, session, seq, pos_counts, speed):
        params = struct.pack("<H", session) + struct.pack("<BHiH", sid, seq & 0xFFFF, int(round(pos_counts * 256)),
                                                         int(max(1, min(65535, speed))))
        err, data = self.transact(sid, EXT_TARGETS, params)
        status = data[0] if data else None
        if status != 0:
            raise BusError("TARGETS rejected: status %s (legacy err 0x%02X)" % (status, err or 0))


class ExtSession:
    """One RoboNine extension session for one servo (start -> moves -> stop)."""

    def __init__(self, bus, sid, cfg):
        self.bus, self.sid, self.cfg = bus, sid, dict(cfg)
        self.session = 0
        self.seq = 0

    def start(self):
        b, sid = self.bus, self.sid
        b.torque(sid, False)
        time.sleep(0.02)
        b.ext_write_config(sid, self.cfg)
        self.session = (int(time.time() * 1000) % 65534) + 1
        b.ext_select(sid, 1, self.session)
        b.torque(sid, True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.5:
            fb = b.ext_feedback(sid)
            if fb["session"] == self.session and fb["fresh"] and fb["drive"] and fb["ready"]:
                self.seq = 0
                return fb
            time.sleep(0.02)
        raise BusError("extension session did not start (check position limits / power / faults)")

    def move(self, pos, speed):
        self.seq = (self.seq + 1) & 0xFFFF
        self.bus.ext_targets(self.sid, self.session, self.seq, pos, speed)
        return self.seq

    def stop(self, to_legacy=True):
        try:
            self.bus.torque(self.sid, False)
            if to_legacy:
                time.sleep(0.02)
                self.bus.ext_select(self.sid, 0, 0)
        finally:
            self.session = 0
