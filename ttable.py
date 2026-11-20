"""官方走时表查询（服务端）。

两张表，按国家选：
  · jma2001 —— 日本气象厅 2001 年走时表（日本国内 EEW 的标准表），
               深度 0–700km、震中距 0–2000km，单位为 km。
  · ak135   —— Kennett (2005) Seismological Tables: ak135（IASPEI/ISC 标准），
               距离 0–125°、深度 0–700km，单位为度。

两张表都由 tools/build_tt_table.py 编译成 data/*.json，查询时按
「距离 × 深度」双线性插值。超出表范围返回 None，由调用方决定兜底。
"""
import json
import math
import math
import os

KM_PER_DEG = 6371.0 * math.pi / 180.0     # 111.1949 km/°

_FILES = {
    "ak135": "tt_ak135.json",
    "jma2001": "tt_jma2001.json",
}
_M = {}
_LOADED = False


def _load():
    global _M, _LOADED
    if _LOADED:
        return
    _LOADED = True
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    for name, fn in _FILES.items():
        try:
            with open(os.path.join(base, fn), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        d["dist"] = [float(x) for x in d.get("dist") or []]
        d["depth"] = [float(x) for x in d.get("depth") or []]
        d["unit"] = d.get("unit") or ("km" if name == "jma2001" else "deg")
        if d["dist"] and d["depth"] and d.get("P") and d.get("S"):
            _M[name] = d


def ready(model="ak135"):
    _load()
    return model in _M


def models():
    _load()
    return sorted(_M)


def km_to_deg(km):
    return max(0.0, float(km or 0.0)) / KM_PER_DEG


def deg_to_km(deg):
    return max(0.0, float(deg or 0.0)) * KM_PER_DEG


def _bracket(arr, x):
    n = len(arr)
    if n < 2:
        return 0, 0.0
    if x <= arr[0]:
        return 0, 0.0
    if x >= arr[-1]:
        return n - 2, 1.0
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if arr[mid] <= x:
            lo = mid
        else:
            hi = mid
    a, b = arr[lo], arr[lo + 1]
    return lo, (x - a) / (b - a) if b > a else 0.0


def _time(d, phase, km, depth):
    """km 一律是震中距（km）；表若以度存储，这里换算。"""
    tab = d.get(phase)
    xs, hs = d["dist"], d["depth"]
    if not tab or not xs:
        return None
    x = max(0.0, float(km or 0.0))
    if d["unit"] != "km":
        x = km_to_deg(x)
    if x > xs[-1]:
        return None
    dep = min(max(0.0, float(depth or 0.0)), hs[-1])
    hi, fy = _bracket(hs, dep)
    di, fx = _bracket(xs, x)
    c0, c1 = tab[hi], tab[hi + 1]
    v00, v01 = c0[di], c0[di + 1]
    v10, v11 = c1[di], c1[di + 1]
    if v00 < 0 or v01 < 0 or v10 < 0 or v11 < 0:
        return None
    top = v00 + (v01 - v00) * fx
    bot = v10 + (v11 - v10) * fx
    return top + (bot - top) * fy


def _dist(d, phase, t, depth):
    tab = d.get(phase)
    xs, hs = d["dist"], d["depth"]
    if not tab or not xs:
        return None
    t = max(0.0, float(t or 0.0))
    dep = min(max(0.0, float(depth or 0.0)), hs[-1])
    hi, fy = _bracket(hs, dep)
    c0, c1 = tab[hi], tab[hi + 1]
    def at(i):
        return c0[i] + (c1[i] - c0[i]) * fy
    if at(0) < 0:
        return None
    if t <= at(0):
        return xs[0]
    if t >= at(len(xs) - 1):
        return xs[-1]
    lo, hi2 = 0, len(xs) - 1
    while hi2 - lo > 1:
        mid = (lo + hi2) // 2
        if at(mid) <= t:
            lo = mid
        else:
            hi2 = mid
    a, b = at(lo), at(hi2)
    f = (t - a) / (b - a) if b > a else 0.0
    v = xs[lo] + (xs[hi2] - xs[lo]) * f
    return v if d["unit"] == "km" else deg_to_km(v)


def time(phase, deg, depth, model="ak135"):
    """走时（秒）。deg 为震中角距；jma2001 表以 km 存储，内部换算。"""
    _load()
    d = _M.get(model)
    if not d:
        return None
    return _time(d, phase, deg_to_km(deg), depth)


def time_km(phase, km, depth, model="ak135"):
    """走时（秒）。km 为震中距（km）。ak135 内部换算成度。

    浅源修正：表里最浅两档是 0 与 15km（JMA2001 是 0 与 10km），直接线性插值在
    近距会明显低估走时（1~10km 的浅震尤其明显）。这里把深度换成「震源距」去查
    0km 那一行，再按深度比例与表值混合——0km 处两者重合，到 15km 归位到表值。
    """
    _load()
    d = _M.get(model)
    if not d:
        return None
    v = _time(d, phase, km, depth)
    hs = d.get("depth") or []
    h = max(0.0, float(depth or 0.0))
    if v is not None and len(hs) > 1 and 0 < h < hs[1]:
        t0 = _time(d, phase, math.hypot(km, h), 0.0)
        if t0 is not None:
            w = h / hs[1]
            v = t0 * (1.0 - w) + v * w
    return v


def dist_km(phase, t, depth, model="ak135"):
    """反解震中距（km）——浅源同样走「震源距」那一支，和 time_km 对称。"""
    _load()
    d = _M.get(model)
    if not d:
        return None
    v = _dist(d, phase, t, depth)
    hs = d.get("depth") or []
    h = max(0.0, float(depth or 0.0))
    if v is not None and len(hs) > 1 and 0 < h < hs[1]:
        hyp = _dist(d, phase, t, 0.0)
        if hyp is not None:
            km_h = math.sqrt(max(0.0, hyp * hyp - h * h))
            w = h / hs[1]
            v = km_h * (1.0 - w) + v * w
    return v


def dist(phase, t, depth, model="ak135"):
    """反解震中角距（度）"""
    km = dist_km(phase, t, depth, model)
    return None if km is None else km_to_deg(km)


def dt(deg, depth, model="ak135"):
    p = time("P", deg, depth, model)
    s = time("S", deg, depth, model)
    return None if (p is None or s is None) else s - p


def dt_km(km, depth, model="ak135"):
    p = time_km("P", km, depth, model)
    s = time_km("S", km, depth, model)
    return None if (p is None or s is None) else s - p


def max_km(model="ak135"):
    _load()
    d = _M.get(model)
    if not d:
        return 0.0
    return d["dist"][-1] if d["unit"] == "km" else deg_to_km(d["dist"][-1])
