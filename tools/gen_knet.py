# -*- coding: utf-8 -*-
"""生成 data/knet_stations.json：NIED K-NET / KiK-net 強震観測点一覧。

约 1750 个真实强震观测点（官方公开的台站名册，含坐标），打包进 exe 后
地图上就有和「强震モニタ」一样密的台网点。可随时重跑刷新（名册每天更新）。
"""
import json
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DST = os.path.join(ROOT, "data", "knet_stations.json")
PAGE = "https://www.kyoshin.bosai.go.jp/ja/stationlist/"
API = "https://www.kyoshin.bosai.go.jp/ja/stationlist/api/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def main():
    html = urllib.request.urlopen(
        urllib.request.Request(PAGE, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8")
    csrf = re.search(r'csrfmiddlewaretoken" value="([^"]+)', html).group(1)
    body = ("csrfmiddlewaretoken=" + csrf + "&datakind=3").encode()
    req = urllib.request.Request(API, data=body, headers={
        "User-Agent": UA, "Referer": PAGE,
        "Cookie": "csrftoken=" + csrf, "X-CSRFToken": csrf,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    d = json.loads(urllib.request.urlopen(req, timeout=180).read())

    rows = []
    for it in d["items"]:
        try:
            la, lo = float(it["lat_jgd"]), float(it["lon_jgd"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (20 <= la <= 46.5 and 122 <= lo <= 154):
            continue
        rows.append([it["sitecode"], it["sitename_j"], it["prefname"],
                     round(la, 4), round(lo, 4), it["data_type_name"]])

    out = {"src": "NIED 強震観測点一覧 (kyoshin.bosai.go.jp)", "stations": rows}
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("stations=%d size=%dKB" % (len(rows), os.path.getsize(DST) // 1024))


if __name__ == "__main__":
    main()
