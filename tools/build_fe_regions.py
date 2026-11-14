"""把 ISC Flinn-Engdahl 分区地名库（E:\\FEregions分区）编译成 data/fe_regions.json。

源文件是 50 个 txt，每行 `编号 | 英文原名 | 中文汉化`，
大区标题 `第N区 English | 中文`。这里只做三件事：
  1. 解析成结构化 JSON（前端/服务端都能直接查）；
  2. 归一化英文名（去掉双空格这类排版瑕疵）；
  3. 政治表述修正：涉台条目一律加「中国」前缀。

用法：
    python tools/build_fe_regions.py ["E:/FEregions分区/FEregions分区"]
"""
import glob
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = sys.argv[1] if len(sys.argv) > 1 else r"E:\FEregions分区\FEregions分区"

# 涉台条目：FE 原译是「台湾 / 台湾地区」，按我们的硬约束改成「中国台湾…」
ZH_FIX = {
    243: "中国台湾地区",      # TAIWAN REGION
    244: "中国台湾",          # TAIWAN
    245: "中国台湾东北",      # NORTHEAST OF TAIWAN
    247: "中国台湾东南",      # SOUTHEAST OF TAIWAN
}


def norm_en(s):
    s = re.sub(r"\s+", " ", s.strip())
    s = s.replace("–", "-").replace("—", "-")
    return s.upper()


def main():
    files = sorted(glob.glob(os.path.join(SRC, "*.txt")))
    if not files:
        raise SystemExit("找不到源文件目录：%s" % SRC)
    areas, rows = [], []
    area = None
    for fp in files:
        for ln in io.open(fp, encoding="utf-8", errors="replace"):
            s = ln.strip()
            if not s or s[0] in "=-":
                continue
            m = re.match(r"^第(\d+)区\s*(.+?)\s*\|\s*(.+)$", s)
            if m:
                area = [int(m.group(1)), norm_en(m.group(2)), m.group(3).strip()]
                areas.append(area)
                continue
            m = re.match(r"^(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)$", s)
            if not m:
                continue
            rid = int(m.group(1))
            en = norm_en(m.group(2))
            zh = m.group(3).strip()
            zh = ZH_FIX.get(rid, zh)
            rows.append([rid, en, zh, area[0] if area else 0])

    rows.sort(key=lambda r: r[0])
    areas.sort(key=lambda a: a[0])
    out = {
        "src": "ISC Flinn-Engdahl Regionalisation Scheme (1995 rev., Young et al. 1996)",
        "n": len(rows),
        "areas": areas,
        "r": rows,
    }
    dst = os.path.join(ROOT, "data", "fe_regions.json")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with io.open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("areas=%d regions=%d -> %s (%.1f KB)" %
          (len(areas), len(rows), dst, os.path.getsize(dst) / 1024.0))


if __name__ == "__main__":
    main()
