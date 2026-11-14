"""历史地震：本地归档 + USGS 补全

- 运行时把 CENC / JMA 的速报持续并入 data/archive.json，重启不丢。
- 时间跨度较大时，再用 USGS FDSN 补全（全球 M4.5+ 完整），两者去重合并。
"""
import json
import os
import sys
import threading
import time
import urllib.parse

import sources

_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = os.path.dirname(sys.executable) if _FROZEN else \
    os.path.dirname(os.path.abspath(__file__))
ARCHIVE_PATH = os.path.join(APP_DIR, "data", "archive.json")
MAX_EVENTS = 4000
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

LOCK = threading.Lock()
ARCHIVE = []
_CACHE = {}
_LOADED = False
_DIRTY = False


def _load():
    global ARCHIVE, _LOADED
    if _LOADED:
        return
    _LOADED = True
    if not os.path.exists(ARCHIVE_PATH):
        return
    try:
        with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            ARCHIVE = data[:MAX_EVENTS]
    except Exception:
        ARCHIVE = []


def _save():
    global _DIRTY
    os.makedirs(os.path.dirname(ARCHIVE_PATH), exist_ok=True)
    tmp = ARCHIVE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ARCHIVE[:MAX_EVENTS], f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, ARCHIVE_PATH)
    _DIRTY = False


def save_if_dirty():
    with LOCK:
        if _DIRTY:
            _save()


def harvest(items):
    """把最新的速报并进归档，返回新增条数"""
    global _DIRTY
    if not items:
        return 0
    _load()
    added = 0
    with LOCK:
        index = {e.get("uid"): e for e in ARCHIVE}
        for it in items:
            uid = it.get("uid")
            if not uid or not it.get("origin_ts"):
                continue
            rec = {
                "uid": uid,
                "country": it.get("country") or "CN",
                "source": it.get("source"),
                "place": it.get("place"),
                "lat": it.get("lat"),
                "lon": it.get("lon"),
                "depth": it.get("depth") or 10,
                "mag": it.get("mag") or 0,
                "max_intensity": it.get("max_intensity"),
                "origin_ts": it.get("origin_ts"),
                "kind": "history",
            }
            old = index.get(uid)
            if old and old.get("mag") == rec["mag"] and old.get("place") == rec["place"]:
                continue
            if old:
                ARCHIVE.remove(old)
            ARCHIVE.append(rec)
            index[uid] = rec
            added += 1
        if added:
            ARCHIVE.sort(key=lambda e: e.get("origin_ts") or 0, reverse=True)
            del ARCHIVE[MAX_EVENTS:]
            _DIRTY = True
    if added:
        save_if_dirty()
    return added


def _dist_km(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * 6371 * asin(min(1, sqrt(a)))


def classify(lat, lon):
    # 中国台湾
    if 21.5 <= lat <= 25.6 and 119.3 <= lon <= 122.2:
        return "CN"
    if 18 <= lat <= 54 and 73 <= lon <= 136:
        return "CN"
    if 24 <= lat <= 46 and 122 <= lon <= 147:
        return "JP"
    return "OT"


CN_SUFFIX = {", Japan": "（日本）", ", China": "（中国）", ", Taiwan": "（中国台湾）",
             ", South Korea": "（韩国）", ", North Korea": "（朝鲜）", ", Russia": "（俄罗斯）",
             ", Philippines": "（菲律宾）", ", Indonesia": "（印尼）", ", Mongolia": "（蒙古）",
             ", Vietnam": "（越南）", ", Myanmar": "（缅甸）"}


def _usgs(from_ts, to_ts, minmag, limit):
    key = (int(from_ts // 3600), int(to_ts // 3600), round(minmag, 1), limit)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]

    q = urllib.parse.urlencode({
        "format": "geojson",
        "starttime": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(from_ts)),
        "endtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(to_ts)),
        "minmagnitude": minmag,
        "minlatitude": 10, "maxlatitude": 56,
        "minlongitude": 70, "maxlongitude": 156,
        "orderby": "time",
        "limit": limit,
    })
    try:
        data = sources.session_json(USGS_URL + "?" + q, timeout=20)
    except Exception:
        _CACHE[key] = (time.time(), [])
        return []

    out = []
    for f in (data.get("features") or []):
        p = f.get("properties") or {}
        g = (f.get("geometry") or {}).get("coordinates") or []
        if len(g) < 2:
            continue
        lat, lon = float(g[1]), float(g[0])
        depth = float(g[2]) if len(g) > 2 and g[2] is not None else 10.0
        place = p.get("place") or "不明"
        cc = None
        for k, v in CN_SUFFIX.items():
            if place.endswith(k):
                place = place[:-len(k)] + v
                cc = "JP" if v == "（日本）" else ("CN" if v in ("（中国）", "（中国台湾）") else "OT")
                break
        out.append({
            "uid": "USGS-" + str(f.get("id")),
            "country": cc or classify(lat, lon),
            "source": "usgs",
            "place": place,
            "lat": round(lat, 3),
            "lon": round(lon, 3),
            "depth": round(depth, 1),
            "mag": round(float(p.get("mag") or 0), 1),
            "max_intensity": None,
            "origin_ts": (p.get("time") or 0) / 1000.0,
            "kind": "history",
            "url": p.get("url"),
        })
    _CACHE[key] = (time.time(), out)
    return out


def stats(days=7, tz=8):
    """近 N 天的本地档案聚合：每日次数/最大震级、区域 Top、震级分布

    只读本地 ARCHIVE，不碰 USGS —— 统计面板要秒开，不能等外网。
    """
    _load()
    now = time.time()
    frm = now - max(1, days) * 86400
    with LOCK:
        items = [dict(e) for e in ARCHIVE if (e.get("origin_ts") or 0) >= frm]
    off = tz * 3600
    buckets = {}
    for e in items:
        d = time.strftime("%Y-%m-%d", time.gmtime((e.get("origin_ts") or 0) + off))
        b = buckets.setdefault(d, {"date": d, "count": 0, "max": 0.0})
        b["count"] += 1
        b["max"] = max(b["max"], e.get("mag") or 0)
    per_day = [buckets[k] for k in sorted(buckets)][-days:]
    # 区域：中国事件有 province，其余按国家
    region = {}
    for e in items:
        key = e.get("province") or {"JP": "日本", "CN": "中国", "TW": "中国台湾"}.get(
            e.get("country"), "其他")
        region[key] = region.get(key, 0) + 1
    by_region = sorted(({"name": k, "count": v} for k, v in region.items()),
                       key=lambda x: -x["count"])[:8]
    mags = [0] * 6          # <3, 3-3.9, 4-4.9, 5-5.9, 6-6.9, >=7
    for e in items:
        m = e.get("mag") or 0
        i = 0 if m < 3 else 1 if m < 4 else 2 if m < 5 else 3 if m < 6 else 4 if m < 7 else 5
        mags[i] += 1
    top = max(items, key=lambda e: e.get("mag") or 0) if items else None
    return {
        "days": days,
        "total": len(items),
        "max_mag": round(top.get("mag") or 0, 1) if top else 0,
        "max_event": top,
        "per_day": per_day,
        "by_region": by_region,
        "mag_buckets": mags,
        "generated": now,
    }


def query(days=30, minmag=4.5, limit=400, local_only=False):
    _load()
    now = time.time()
    from_ts = now - days * 86400
    with LOCK:
        native = [dict(e) for e in ARCHIVE
                  if (e.get("origin_ts") or 0) >= from_ts and (e.get("mag") or 0) >= minmag]
    out = native
    # local_only：统计面板导 CSV 用，别为了补几条 USGS 事件等十几秒外网
    usgs = [] if local_only else (_usgs(from_ts, now, minmag, 800) if days >= 1 else [])

    # 去重：USGS 与本地同一次地震（150km / 180s 内）只留本地
    kept = []
    for u in usgs:
        if (u.get("mag") or 0) < minmag:
            continue
        dup = False
        for n in native:
            if abs((n.get("origin_ts") or 0) - u["origin_ts"]) > 180:
                continue
            try:
                if _dist_km(n["lat"], n["lon"], u["lat"], u["lon"]) < 150:
                    dup = True
                    break
            except (TypeError, KeyError):
                continue
        if not dup:
            kept.append(u)

    out = native + kept
    out.sort(key=lambda e: e.get("origin_ts") or 0, reverse=True)
    return {
        "count": len(out),
        "days": days,
        "minmag": minmag,
        "archive": len(native),
        "usgs": len(kept),
        "items": out[:limit],
        "generated": now,
    }
