"""Qwen dial-gauge reader (OpenAI-compatible chat/completions), same server, prompt and key as the MOTORON bench.

Defaults: MOTORON_VISION_URL or http://172.16.0.30:4000/v1, model VISION_MODEL or "vision", key MOTORON_VISION_KEY
(process env, then HKCU\\Environment). Works unchanged with Ollama (http://localhost:11434/v1, e.g. qwen2.5vl:7b, no key).
The key is never logged or written to disk.
Prompt: prompts/gauge_reader.txt + the 7-segment rules block (copied verbatim from MOTORON advisors/prompts).
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = os.environ.get("MOTORON_VISION_URL") or "http://172.16.0.30:4000/v1"
DEFAULT_MODEL = os.environ.get("VISION_MODEL") or "vision"


def load_prompt():
    with open(os.path.join(HERE, "prompts", "gauge_reader.txt"), encoding="utf-8") as f:
        text = f.read()
    with open(os.path.join(HERE, "prompts", "gauge_reader_7seg.txt"), encoding="utf-8") as f:
        extra = f.read().strip("\n")
    return text.replace("<output_format>", extra + "\n\n<output_format>", 1)


def api_key():
    k = (os.environ.get("MOTORON_VISION_KEY") or "").strip()
    if k or os.name != "nt":
        return k or None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            v, _ = winreg.QueryValueEx(h, "MOTORON_VISION_KEY")
        return str(v).strip() or None
    except Exception:
        return None


def parse_value(raw_text):
    """Value from the characters the model read (its own 'value' field is not trusted). Keeps the decimals shown."""
    s = str(raw_text or "").strip().replace("−", "-").replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not re.fullmatch(r"-?\d{0,3}\.?\d{1,4}", s or "x"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fixed_decimals(raw_text, decimals):
    """The gauge always shows `decimals` digits after the point. The LCD point is faint and Qwen often drops or
    moves it (0.99 -> '099', 1.01 -> '10.1'), so use only the digits and place the point ourselves."""
    s = str(raw_text or "").replace("−", "-").strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) < int(decimals) + 1 or len(digits) > 7:     # '14' on a 0.00 gauge = a digit was missed
        return None
    v = int(digits) / 10 ** int(decimals)
    return -v if s.startswith("-") else v


def apply_decimals(res, decimals):
    """Re-derive res['value'] from raw_text when the gauge's decimal count is known."""
    if decimals in (None, "") or res.get("status") != "ok" or not re.search(r"\d", str(res.get("raw_text") or "")):
        return res
    v = fixed_decimals(res["raw_text"], decimals)
    if v is not None:
        res["value"], res["unreadable"] = v, False
    return res


def parse_reply(txt):
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip())
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    obj = json.loads(s)
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    raw = obj.get("raw_text")
    unreadable = bool(obj.get("unreadable"))
    val = None if unreadable else parse_value(raw)
    return dict(raw_text=raw, value=val, model_value=obj.get("value"), confidence=obj.get("confidence"),
                unreadable=unreadable or val is None, notes=obj.get("notes") or "")


class QwenReader:
    def __init__(self, url=None, model=None, thinking=False):
        self.url = (url or DEFAULT_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.thinking = thinking
        self.prompt = load_prompt()

    def describe(self):
        return "%s @ %s" % (self.model, self.url)

    def read(self, jpg_bytes):
        """Never raises. -> dict(status ok|error, value, raw_text, confidence, unreadable, notes, ms)."""
        t0 = time.monotonic()
        body = {"model": self.model, "temperature": 0, "max_tokens": 4000 if self.thinking else 400,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": self.prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," +
                                                        base64.b64encode(jpg_bytes).decode("ascii")}}]}]}
        if not self.thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        key = api_key()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(self.url + "/chat/completions", data=json.dumps(body).encode(), headers=headers,
                                     method="POST")
        ms = lambda: round(1000 * (time.monotonic() - t0))
        try:
            with urllib.request.urlopen(req, timeout=180 if self.thinking else 45) as r:
                resp = json.loads(r.read().decode("utf-8"))
            txt = resp["choices"][0]["message"]["content"] or ""
            out = parse_reply(txt)
            out.update(status="ok", ms=ms(), raw=txt[:500])
            return out
        except urllib.error.HTTPError as e:
            msg = {401: "401 bad key (set MOTORON_VISION_KEY)", 404: "404 unknown model %s" % self.model}.get(
                e.code, "HTTP %d" % e.code)
        except (urllib.error.URLError, OSError) as e:
            msg = "vision server unreachable (%s)" % type(e).__name__
        except (ValueError, KeyError, IndexError, TypeError) as e:
            msg = "bad reply: %s" % str(e)[:120]
        if key:
            msg = msg.replace(key, "<key>")
        return dict(status="error", value=None, raw_text=None, confidence=None, unreadable=True, notes=msg, ms=ms())
