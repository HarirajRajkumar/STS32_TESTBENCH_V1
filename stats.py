"""Repeatability statistics for one run (pure functions, no I/O)."""
import math

ARCSEC_PER_RAD = 206264.806
DEG_PER_COUNT = 360.0 / 4096.0


def describe(vals):
    v = [float(x) for x in vals if x is not None]
    n = len(v)
    if n == 0:
        return dict(n=0)
    mean = sum(v) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1)) if n > 1 else 0.0
    return dict(n=n, mean=mean, sd=sd, min=min(v), max=max(v), range=max(v) - min(v), rp3s=3 * sd)


def final_value(row):
    """Operator correction wins over Qwen; rows flagged 'exclude' are dropped."""
    if row.get("exclude"):
        return None
    m = row.get("manual_value")
    if m not in (None, ""):
        return float(m)
    return row.get("qwen_value")


def summarise(rows, lever_r_mm=0.0, skip_first=0):
    rows = [r for r in rows if r.get("cycle", 0) > skip_first]
    out = {"groups": {}, "lever_r_mm": lever_r_mm, "skip_first": skip_first}
    for d in sorted({r["dir"] for r in rows}):
        rr = [r for r in rows if r["dir"] == d]
        g = {"gauge_mm": describe([final_value(r) for r in rr]),
             "encoder_err_counts": describe([r.get("err_counts") for r in rr]),
             "unread": sum(1 for r in rr if final_value(r) is None and not r.get("exclude")),
             "pending": sum(1 for r in rr if r.get("qwen_status") == "pending")}
        gm = g["gauge_mm"]
        if lever_r_mm and lever_r_mm > 0 and gm.get("n"):
            k = ARCSEC_PER_RAD / lever_r_mm      # small angle: theta = s / r
            g["gauge_arcsec"] = {key: (gm[key] * k if key != "n" else gm[key]) for key in gm}
        ge = g["encoder_err_counts"]
        if ge.get("n"):
            g["encoder_err_deg"] = {key: (ge[key] * DEG_PER_COUNT if key != "n" else ge[key]) for key in ge}
        out["groups"][d] = g
    allv = [final_value(r) for r in rows]
    out["all_gauge_mm"] = describe(allv)
    gp, gn = out["groups"].get("+"), out["groups"].get("-")
    if gp and gn and gp["gauge_mm"].get("n") and gn["gauge_mm"].get("n"):
        lm = gp["gauge_mm"]["mean"] - gn["gauge_mm"]["mean"]
        out["lost_motion_mm"] = lm
        if lever_r_mm and lever_r_mm > 0:
            out["lost_motion_arcsec"] = lm / lever_r_mm * ARCSEC_PER_RAD
        out["lost_motion_encoder_counts"] = (gp["encoder_err_counts"].get("mean", 0) -
                                             gn["encoder_err_counts"].get("mean", 0))
    return out
