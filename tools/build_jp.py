"""把日本 47 都道府县边界简化，产出 web/geo_jp.js

数据：dataofjapan/land japan.geojson
curl -sL --compressed -o _jp.geojson \
  https://cdn.jsdelivr.net/gh/dataofjapan/land@master/japan.geojson
"""
import json
import math
import os
import sys

SRC = "_jp.geojson"
OUT = "../web/geo_jp.js"
TOL = float(sys.argv[1]) if len(sys.argv) > 1 else 0.008
MIN_AREA = 0.008          # 丢掉太小的离岛（平方度）


def dp(pts, tol):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = math.hypot(px - ax, py - ay) if norm == 0 else \
                abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, bi = d, i
        if best > tol and bi > 0:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def area(r):
    s = 0.0
    for i in range(len(r) - 1):
        s += r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]
    return abs(s) / 2


def centroid(ring):
    """环的面积质心"""
    a = cx = cy = 0.0
    for i in range(len(ring) - 1):
        f = ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        a += f
        cx += (ring[i][0] + ring[i + 1][0]) * f
        cy += (ring[i][1] + ring[i + 1][1]) * f
    if abs(a) < 1e-9:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return [sum(xs) / len(xs), sum(ys) / len(ys)]
    a *= 0.5
    return [cx / (6 * a), cy / (6 * a)]


feats = json.load(open(SRC, encoding="utf-8"))["features"]
out = []
total = 0

for f in feats:
    name = f["properties"].get("nam_ja") or f["properties"].get("nam")
    g = f["geometry"]
    if not g:
        continue
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    rings = []
    biggest, biggest_a = None, 0.0
    for poly in polys:
        outer = poly[0]
        if area(outer) < MIN_AREA:
            continue
        simp = dp(outer, TOL)
        if len(simp) < 4:
            continue
        closed = [[round(x, 3), round(y, 3)] for x, y in simp]
        if closed[0] != closed[-1]:
            closed.append(closed[0])
        rings.append(closed[:-1])
        if area(closed) > biggest_a:
            biggest, biggest_a = closed, area(closed)
    if not rings:
        continue
    rings.sort(key=area, reverse=True)
    total += sum(len(r) for r in rings)
    out.append({"n": name, "c": [round(v, 2) for v in centroid(biggest)], "p": rings})

# 大的排前面，绘制时小的压在上面
out.sort(key=lambda p: -sum(area(r) for r in p["p"]))
data = {"tol": TOL, "prefs": out}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fp:
    fp.write("window.GEOJP=")
    json.dump(data, fp, separators=(",", ":"), ensure_ascii=False)
    fp.write(";\n")

print("prefs:", len(out), "points:", total, "size:", os.path.getsize(OUT) // 1024, "KB",
      "tol:", TOL)
