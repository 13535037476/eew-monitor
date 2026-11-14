# -*- coding: utf-8 -*-
"""重建地图基础数据：web/geo_world.js（全球海岸线）+ web/geo.js（东亚物理陆地+中国标准边界）

数据源（先下载到本目录）：
  ne50_land.json   Natural Earth 50m 陆地（全球海岸线）
                   curl -L -o ne50_land.json https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson
  ne10_land.json   Natural Earth 10m 陆地（东亚精细海岸线）
                   curl -L -o ne10_land.json https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson
  cn_outline.json  DataV 全国边界（含官方藏南边界/台湾/海南/南海诸岛）
                   curl -L -o cn_outline.json https://geo.datav.aliyun.com/areas_v3/bound/100000.json
  cn_full.json     DataV 分省边界（取 100000_JD 九段线要素）
                   curl -L -o cn_full.json https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json

设计要点（v1.9.16）：
  * 陆地层一律用「物理陆地」数据（NE 10m/50m）——国界不参与陆地填充，
    杜绝「中国官方线 + NE 印度环事实线」两条平行怪线、以及 evenodd 双环抵消挖空。
  * 中国国界单独存 geo.cn（DataV 标准数据，只描线不填充），前端叠加在陆地上。
  * DataV 的海南/台湾/港澳/南海诸岛/钓鱼岛等岛礁环保留为填充（10m 陆地里缺的小岛礁补齐）。
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.normpath(os.path.join(HERE, "..", "web"))

# 东亚层覆盖范围（比默认东亚视野略大）
EA_BOX = (68.0, 170.0, -14.0, 58.0)   # lon0, lon1, lat0, lat1


def load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def dp(pts, tol):
    """Douglas-Peucker"""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]; bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if norm == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, bi = d, i
        if best > tol and bi > 0:
            keep[bi] = True
            stack.append((a, bi)); stack.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def ring_area(r):
    s = 0.0
    for i in range(len(r) - 1):
        s += r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]
    return abs(s) / 2


def clean_ring(pts, tol):
    """四舍五入 2 位、去重复、DP 简化、保留闭合"""
    out = []
    for p in pts:
        q = [round(p[0], 2), round(p[1], 2)]
        if out and q == out[-1]:
            continue
        out.append(q)
    if out and out[0] == out[-1]:
        out = out[:-1]
    out = dp(out, tol)
    if len(out) >= 3:
        out.append(out[0])
    return out if len(out) >= 4 else None


def rings_of(geom):
    t = geom["type"]
    if t == "Polygon":
        return [geom["coordinates"][0]]
    if t == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


def ring_bbox(r):
    xs = [p[0] for p in r]; ys = [p[1] for p in r]
    return min(xs), max(xs), min(ys), max(ys)


def split_antimeridian(pts):
    """把跨 ±180° 经线的折线拆成连续段（每段经度连续，跳变处断开）"""
    segs, cur = [], [pts[0]]
    for i in range(1, len(pts)):
        if abs(pts[i][0] - cur[-1][0]) > 180:   # 跨反子午线，断开
            segs.append(cur)
            cur = [pts[i]]
        else:
            cur.append(pts[i])
    segs.append(cur)
    return [s for s in segs if len(s) >= 2]


# ---------- 1. geo_world.js：NE 50m 全球陆地 ----------

def build_world():
    d = load("ne50_land.json")
    rings, dropped = [], 0
    for f in d["features"]:
        for r in rings_of(f["geometry"]):
            if min(p[1] for p in r) < -60:      # 丢南极洲（墨卡托极区难看）
                dropped += 1
                continue
            for seg in split_antimeridian(r):   # 跨 ±180° 的环拆开，避免绘制自交
                ring = clean_ring(seg, 0.02)
                if ring and ring_area(ring) >= 0.02:
                    rings.append(ring)
    rings.sort(key=ring_area, reverse=True)
    data = {"land": rings}
    out = os.path.join(WEB, "geo_world.js")
    with open(out, "w", encoding="utf-8") as fp:
        fp.write("window.GEOW=")
        json.dump(data, fp, separators=(",", ":"))
        fp.write(";\n")
    print("geo_world.js: %d rings, %d pts, %d KB (antarctic dropped %d)"
          % (len(rings), sum(len(p) for p in rings),
             os.path.getsize(out) // 1024, dropped))


# ---------- 2. geo.js：东亚物理陆地（NE 10m）+ 中国标准边界 ----------

def build_eastasia():
    # 2a. 物理陆地：NE 10m，裁东亚范围。国界不进陆地——藏南/阿克赛钦天然是连续陆地
    d = load("ne10_land.json")
    lon0, lon1, lat0, lat1 = EA_BOX
    land = []
    for f in d["features"]:
        for r in rings_of(f["geometry"]):
            bb = ring_bbox(r)
            if bb[1] < lon0 or bb[0] > lon1 or bb[3] < lat0 or bb[2] > lat1:
                continue                          # 与东亚范围完全不相交
            for seg in split_antimeridian(r):
                ring = clean_ring(seg, 0.006)
                # 物理层不收微小岛礁（中国的岛礁由 DataV 补齐，精度更高）
                if ring and ring_area(ring) >= 0.01:
                    land.append(ring)

    # 2b. 中国标准边界：DataV 全国轮廓（官方藏南边界），只描线不填充
    cn_geom = load("cn_outline.json")["features"][0]["geometry"]
    cn_lines = []
    for r in rings_of(cn_geom):
        bb = ring_bbox(r)
        if bb[1] < lon0 or bb[0] > lon1 or bb[3] < lat0 or bb[2] > lat1:
            continue
        ring = clean_ring(r, 0.002)
        if ring:
            cn_lines.append(ring)

    # 2c. 中国岛礁填充环：从 DataV 全国轮廓里取「非主环」的小环（海南/台湾/诸岛）
    #     主环（中国大陆）已在物理陆地中，重复填充会在重叠区出问题；只收面积 < 主环的岛礁
    cn_main = max(cn_lines, key=len)
    islands = []
    for ring in cn_lines:
        if ring is cn_main or len(ring) < 4:
            continue
        bb = ring_bbox(ring)
        w, h = bb[1] - bb[0], bb[3] - bb[2]
        if w > 40 and h > 20:                     # 主环特征（跨 40°+），跳过
            continue
        islands.append(ring)

    # 2d. 九段线（100000_JD 要素，细长多边形取外环当线画）
    dashes = []
    for f in load("cn_full.json")["features"]:
        if f.get("properties", {}).get("adcode") == "100000_JD":
            for r in rings_of(f["geometry"]):
                ring = clean_ring(r, 0.01)
                if ring:
                    dashes.append(ring[:-1])      # 线不闭合

    geo = {"land": land, "cn": cn_main, "cnisl": islands, "scs": dashes}
    out = os.path.join(WEB, "geo.js")
    with open(out, "w", encoding="utf-8") as fp:
        fp.write("window.GEO=")
        json.dump(geo, fp, separators=(",", ":"))
        fp.write(";\n")
    print("geo.js: 陆地 %d 环/%d 点, 国界线 %d 点, 岛礁 %d 环, 九段线 %d 段, %d KB"
          % (len(land), sum(len(p) for p in land), len(cn_main),
             len(islands), len(dashes), os.path.getsize(out) // 1024))


if __name__ == "__main__":
    build_world()
    build_eastasia()
