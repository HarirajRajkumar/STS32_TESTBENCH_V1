"""Camera: one capture thread, latest frame kept in memory, MJPEG for the browser, ROI crop for Qwen."""
import threading
import time

import cv2


def list_cameras(max_index=6):
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
        if ok:
            found.append(i)
    return found


class Camera:
    def __init__(self):
        self.cap = None
        self.index = None
        self.frame = None
        self.frame_t = 0.0
        self.lock = threading.Lock()
        self.running = False
        self.thread = None
        self.error = ""
        self.rotate = 0          # 0 / 90 / 180 / 270 degrees clockwise
        self.flip = "none"       # none / h (mirror left-right) / v (mirror top-bottom)

    def orient(self, img):
        """Applied to every frame at capture, so preview, gauge box, Qwen crop and saved frames all agree."""
        r = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(self.rotate)
        if r is not None:
            img = cv2.rotate(img, r)
        if self.flip == "h":
            img = cv2.flip(img, 1)
        elif self.flip == "v":
            img = cv2.flip(img, 0)
        return img

    def open(self, index, width=1280, height=720):
        self.close()
        cap = cv2.VideoCapture(int(index), cv2.CAP_DSHOW)
        if not cap.isOpened():
            self.error = "camera %s did not open" % index
            raise RuntimeError(self.error)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap, self.index, self.error = cap, int(index), ""
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self.thread.start()

    def _loop(self):
        while self.running:
            ok, img = self.cap.read()
            if not ok:
                self.error = "camera read failed"
                time.sleep(0.1)
                continue
            img = self.orient(img)
            with self.lock:
                self.frame, self.frame_t = img, time.time()

    def close(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.cap:
            self.cap.release()
        self.cap, self.thread, self.frame = None, None, None

    def latest(self):
        with self.lock:
            return (None, 0.0) if self.frame is None else (self.frame.copy(), self.frame_t)

    def fresh_frame(self, after_t, timeout=1.0):
        """A frame captured strictly after `after_t` (so the gauge image is taken after settling, not from the buffer)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            img, t = self.latest()
            if img is not None and t > after_t:
                return img, t
            time.sleep(0.01)
        return self.latest()

    def mjpeg(self, fps=15):
        period = 1.0 / fps
        while True:
            img, _ = self.latest()
            if img is not None:
                ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
            time.sleep(period)


def crop_roi(img, roi, grow=0.10, long_side=1024):
    """roi = {x,y,w,h} as fractions of the frame. Box grown by 10 %, long side <= 1024 px (same as MOTORON repeat.js)."""
    if img is None:
        return None
    H, W = img.shape[:2]
    if not roi or roi.get("w", 0) <= 0 or roi.get("h", 0) <= 0:
        out = img
    else:
        x, y, w, h = roi["x"] * W, roi["y"] * H, roi["w"] * W, roi["h"] * H
        gx, gy = w * grow / 2, h * grow / 2
        x0, y0 = max(0, int(x - gx)), max(0, int(y - gy))
        x1, y1 = min(W, int(x + w + gx)), min(H, int(y + h + gy))
        out = img[y0:y1, x0:x1]
    h, w = out.shape[:2]
    s = long_side / max(h, w)
    if s < 1.0:
        out = cv2.resize(out, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return out


def jpeg_bytes(img, q=92):
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return jpg.tobytes() if ok else None
