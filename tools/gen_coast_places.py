# -*- coding: utf-8 -*-
"""从市町村表里挑出「沿海」的地名，给海啸预报做地名级判定。

思路：市町村代表点落在陆地上，向 24 个方位 × 若干半径找海。
- 半径 1.5~9km 内没有任何海点 → 内陆市町村，不参与海啸预报（群马/长野那种）
- 找到海点后，再沿海点方向往外延伸 20km：如果一路都是陆地，说明是湖（琵琶湖、
  霞ヶ浦这类），也不算沿海；能连着水面的才算海岸。

陆地判定用扫描线栅格：把 ne10 的每一条边按 0.02°（约 2km）纬度行求交点，
判定时对行内交点做二分 + 奇偶，单次 ~微秒级，比逐环射线法快三个数量级。

输出 data/coast_places.json：

    {"prefs": {"宮城県": [["石巻市", 38.43, 141.30, 141.38, 38.40], ...]}}

字段：[市町村名, 纬, 经, 岸点经, 岸点纬]（岸点＝向海挪 1.5km，画标记用）

    python tools/gen_coast_places.py
"""
import bisect
import io
import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAND = os.path.join(ROOT, "tools", "ne10_land.json")
PLACES = os.path.join(ROOT, "data", "jp_places.json")
OUT = os.path.join(ROOT, "data", "coast_places.json")

KM_LAT = 111.32
LAT0, LAT1 = 20.0, 47.0          # 日本列岛范围
LON0, LON1 = 122.0, 152.0
STEP = 0.02                       # 纬度行间距（约 2.2km）
NROW = int((LAT1 - LAT0) / STEP) + 1


def load_rings(path):
    g = json.load(io.open(path, encoding="utf-8"))
    rings = []
    for f in g["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for ring in poly:
                if len(ring) >= 3:
                    rings.append([(float(p[0]), float(p[1])) for p in ring])
    return rings


def build_rows(rings):
    """rows[r] = 该纬度行上所有边的穿越经度（已排序）"""
    rows = [[] for _ in range(NROW)]
    for ring in rings:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if max(ys) < LAT0 or min(ys) > LAT1:
            continue
        if max(xs) < LON0 or min(xs) > LON1:
            continue
        n = len(ring)
        for k in range(n):
            ax, ay = ring[k]
            bx, by = ring[(k + 1) % n]
            if ay == by:
                continue
            lo, hi = (ay, by) if ay < by else (by, ay)
            # 行中心 yc = LAT0+(r+0.5)*STEP，要求 lo <= yc < hi（半开，顶点不重复计数）
            r0 = max(0, int(math.floor((lo - LAT0) / STEP - 0.5)) + 1)
            r1 = min(NROW - 1, int(math.floor((hi - LAT0) / STEP - 0.5)))
            for r in range(r0, r1 + 1):
                yc = LAT0 + (r + 0.5) * STEP
                rows[r].append(ax + (yc - ay) * (bx - ax) / (by - ay))
    for r in rows:
        r.sort()
    return rows


def make_is_land(rows):
    def hit(lon, lat):
        if not (LAT0 <= lat < LAT1 and LON0 <= lon <= LON1):
            return False
        xs = rows[int((lat - LAT0) / STEP)]
        if not xs:
            return False
        return (bisect.bisect_left(xs, lon) & 1) == 1
    return hit


def offset(lat, lon, az_deg, km):
    """从 (lat,lon) 朝 az_deg（0=北，顺时针）走 km 公里"""
    a = math.radians(az_deg)
    dlat = km * math.cos(a) / KM_LAT
    dlon = km * math.sin(a) / (KM_LAT * max(0.2, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def main():
    rings = load_rings(LAND)
    t_rows = build_rows(rings)
    is_land = make_is_land(t_rows)
    print("陆地环 %d，扫描行 %d，行内交点 %d" % (
        len(rings), NROW, sum(len(r) for r in t_rows)))

    raw = json.load(io.open(PLACES, encoding="utf-8"))["prefs"]
    out = {}
    total = kept = 0
    for pref, towns in raw.items():
        arr = []
        for t in towns:
            total += 1
            name, lat, lon = str(t[0]), float(t[1]), float(t[2])
            if not is_land(lon, lat):
                continue                      # 代表点落在水里，跳过
            best = None
            for km in (1.5, 3, 5, 7, 9):
                for az in range(0, 360, 15):
                    plat, plon = offset(lat, lon, az, km)
                    if is_land(plon, plat):
                        continue
                    # 湖 vs 海：同方位再走 20km，仍是水面才算开阔海
                    plat2, plon2 = offset(plat, plon, az, 20)
                    if is_land(plon2, plat2):
                        continue
                    best = (km, az, plon, plat)
                    break
                if best:
                    break
            if not best:
                continue
            km, az, slon, slat = best
            shlat, shlon = offset(slat, slon, (az + 180) % 360, 1.5)   # 贴回岸线
            arr.append([name, round(lat, 4), round(lon, 4), round(shlon, 4), round(shlat, 4)])
            kept += 1
        if arr:
            out[pref] = arr

    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps({"prefs": out}, ensure_ascii=False, separators=(",", ":")))
    print("市町村 %d，沿海 %d，县 %d → %s (%.1f KB)" % (
        total, kept, len(out), OUT, os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    main()
