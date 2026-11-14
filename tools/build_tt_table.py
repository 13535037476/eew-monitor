"""把官方走时表编译成 EEW 用的紧凑数据（AK135 + JMA2001）。

数据来源（公开、可核对）：
  B. L. N. Kennett (2005), Seismological Tables: ak135,
  Research School of Earth Sciences, ANU / IASPEI
  https://download.iaspei.org/download/AK135tables.pdf
  tools/ak135_src/*.txt 是该 PDF 的机读转录（来自 bclswl0827/travel 的
  resource/data，附带说明见 README.txt）。

表结构：每个相位一张 距离×深度 的走时表
  · 距离 0–2° 按 0.1° 采样，3–125° 按 1° 采样
  · 浅源深度 0/15/35/50/100/150/200/250 km，深源 300–700 km 每 50 km
  · 数值单位秒，缺失为 -1（本版数据没有缺失）

另外编译 JMA2001（日本气象厅 2001 年走时表，日本国内 EEW 的标准表）：
  tools/jma2001_src/tjma2001  来自 JMA 官方公开的 tjma2001.zip
  https://www.data.jma.go.jp/eqev/data/bulletin/catalog/appendix/trtime/
  网格：深度 0–700km、震中距 0–2000km（近距 2km、远距 10km）
  → data/tt_jma2001.json / web/tt_jma.js（window.TTJMA）

输出：
  data/tt_ak135.json    + web/tt.js      （全球 / 日本以外）
  data/tt_jma2001.json  + web/tt_jma.js  （日本国内，JMA 自己的表）
服务端与前端读同一份数据，保证「画圈」和「台站亮起」用的是同一张表。

用法：python tools/build_tt_table.py
"""
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "tools", "ak135_src")
OUT_JSON = os.path.join(ROOT, "data", "tt_ak135.json")
OUT_JS = os.path.join(ROOT, "web", "tt.js")


def parse(path):
    lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
    i = 0
    while i < len(lines) and not (lines[i].startswith("∆") or lines[i].startswith("Δ")):
        i += 1
    if i >= len(lines):
        raise RuntimeError("找不到表头: %s" % path)
    depths = [float(x) for x in re.findall(r"(\d+\.)", lines[i])]
    i += 2                          # 跳过「m s m s」单位行
    rows = {}
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        parts = s.split()
        try:
            dist = float(parts[0])
        except ValueError:
            i += 1
            continue
        vals = parts[1:]
        times = []
        for k in range(0, len(vals) - 1, 2):
            try:
                times.append(int(vals[k]) * 60 + float(vals[k + 1]))
            except ValueError:
                times.append(None)
        rows[dist] = times
        i += 2                      # 下一行是射线参数（这里不用）
    return depths, rows


def merge(shallow, deep):
    d1, r1 = shallow
    d2, r2 = deep
    depths = d1 + d2
    dists = sorted(set(r1) | set(r2))
    table = []
    for dep_i in range(len(depths)):
        col = []
        for d in dists:
            if dep_i < len(d1):
                arr = r1[d]
            else:
                arr = r2[d]
            k = dep_i if dep_i < len(d1) else dep_i - len(d1)
            v = arr[k] if k < len(arr) else None
            col.append(round(v, 2) if v is not None else -1)
        table.append(col)
    return depths, dists, table


# ---------------- JMA2001 ----------------
JMA_SRC = os.path.join(ROOT, "tools", "jma2001_src", "tjma2001")
OUT_JMA_JSON = os.path.join(ROOT, "data", "tt_jma2001.json")
OUT_JMA_JS = os.path.join(ROOT, "web", "tt_jma.js")
# 输出网格：近场 5km（波前刚起步时别太糙）、远场 10km；深度浅部 10km、深部 25–50km
JMA_DIST = list(range(0, 201, 5)) + list(range(210, 2001, 10))
JMA_DEPTH = [0, 10, 20, 30, 40, 50, 60, 80, 100, 125, 150, 175, 200,
             250, 300, 350, 400, 450, 500, 550, 600, 650, 700]


def parse_jma(path):
    rows = {}
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            s = ln.split()
            if len(s) < 6 or s[0] != "P" or s[2] != "S":
                continue
            try:
                tp, ts = float(s[1]), float(s[3])
                dep, dist = int(s[4]), int(s[5])
            except ValueError:
                continue
            rows[(dep, dist)] = (tp, ts)
    return rows


def _interp1(xs, ys, x):
    """xs 升序、ys 对应；线性插值（x 超出范围时取端点）"""
    if x <= xs[0]:
        return ys[0] + (ys[1] - ys[0]) * (x - xs[0]) / (xs[1] - xs[0])
    if x >= xs[-1]:
        return ys[-2] + (ys[-1] - ys[-2]) * (x - xs[-2]) / (xs[-1] - xs[-2])
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        m = (lo + hi) // 2
        if xs[m] <= x:
            lo = m
        else:
            hi = m
    return ys[lo] + (ys[hi] - ys[lo]) * (x - xs[lo]) / (xs[hi] - xs[lo])


def build_jma():
    rows = parse_jma(JMA_SRC)
    if not rows:
        raise RuntimeError("JMA2001 源文件解析为空")
    deps = sorted({k[0] for k in rows})
    dists = sorted({k[1] for k in rows})
    P, S = [], []
    for dep in JMA_DEPTH:
        cp, cs = [], []
        for dist in JMA_DIST:
            # 先按距离双向插值到每个源深度，再按深度插值
            pcol = [_interp1(dists, [rows[(d, x)][0] for x in dists], dist) for d in deps]
            scol = [_interp1(dists, [rows[(d, x)][1] for x in dists], dist) for d in deps]
            cp.append(round(_interp1(deps, pcol, dep), 2))
            cs.append(round(_interp1(deps, scol, dep), 2))
        P.append(cp)
        S.append(cs)
    data = {
        "model": "jma2001",
        "ref": "JMA2001 travel time table (JMA, 2001; public bulletin appendix)",
        "unit": "km",
        "dist": JMA_DIST,
        "depth": JMA_DEPTH,
        "P": P,
        "S": S,
    }
    with io.open(OUT_JMA_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    js = "/* JMA2001 走时表（日本气象厅，公开资料），由 tools/build_tt_table.py 生成，勿手改 */\n" \
         "window.TTJMA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    with io.open(OUT_JMA_JS, "w", encoding="utf-8") as f:
        f.write(js)
    print("JMA2001 depths=%d dists=%d json=%.1fKB js=%.1fKB"
          % (len(JMA_DEPTH), len(JMA_DIST),
             os.path.getsize(OUT_JMA_JSON) / 1024.0, os.path.getsize(OUT_JMA_JS) / 1024.0))


def main():
    P = merge(parse(os.path.join(SRC, "ak135_P_shallow.txt")),
              parse(os.path.join(SRC, "ak135_P_deep.txt")))
    S = merge(parse(os.path.join(SRC, "ak135_S_shallow.txt")),
              parse(os.path.join(SRC, "ak135_S_deep.txt")))
    if P[1] != S[1]:
        raise RuntimeError("P/S 距离采样不一致")
    data = {
        "model": "ak135",
        "ref": "Kennett (2005) Seismological Tables: ak135 (IASPEI)",
        "unit": "s",
        "dist": P[1],
        "depth": P[0],
        "P": P[2],
        "S": S[2],
    }
    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    js = "/* AK135 走时表（Kennett 2005 / IASPEI），由 tools/build_tt_table.py 生成，勿手改 */\n" \
         "window.TT135 = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    with io.open(OUT_JS, "w", encoding="utf-8") as f:
        f.write(js)
    n = len(data["dist"])
    print("AK135 depths=%d dists=%d P=%dx%d S=%dx%d  json=%.1fKB js=%.1fKB"
          % (len(data["depth"]), n, len(data["P"]), n, len(data["S"]), n,
             os.path.getsize(OUT_JSON) / 1024.0, os.path.getsize(OUT_JS) / 1024.0))
    build_jma()


if __name__ == "__main__":
    main()
