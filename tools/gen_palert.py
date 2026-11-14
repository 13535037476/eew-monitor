"""拉 P-Alert 官方测站表，存成前端用的小文件 data/palert_stations.json。

  python tools/gen_palert.py

GraphQL 走官方 realtime 页同一条 query（token 传空串），786 站在线。
只留画点需要的字段：站号 / 地名 / 县市 / 经纬度。
"""
import json
import os
import ssl
import sys
import time
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "palert_stations.json")
Q = ("query ($staFilter: staList_filter_choices) { stationList(staFilter: $staFilter) "
     "{ staInfos timestamp version } }")


def main():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # 证书缺 SKI，见 sources.palert_realtime 注释
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPSHandler(context=ctx))
    body = json.dumps({"query": Q, "variables": {"staFilter": "onlineAll"}}).encode()
    req = urllib.request.Request(
        "https://palert.earth.sinica.edu.tw/graphql/", data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://palert.earth.sinica.edu.tw/realtime",
            "User-Agent": "Mozilla/5.0",
        })
    t0 = time.time()
    with op.open(req, timeout=25) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    sl = (d.get("data") or {}).get("stationList") or {}
    out = []
    for x in sl.get("staInfos") or []:
        lon, lat = x.get("lon"), x.get("lat")
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        out.append({
            "c": str(x.get("station") or "").strip(),
            "n": str(x.get("locname") or "").strip(),
            "a": str(x.get("area") or "").strip(),
            "x": round(float(lon), 4),
            "y": round(float(lat), 4),
        })
    out = [s for s in out if s["c"]]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"version": sl.get("version"), "ts": sl.get("timestamp"),
                   "stations": out}, f, ensure_ascii=False, separators=(",", ":"))
    print("%.1fs  %d stations -> %s (%.1f KB)" % (
        time.time() - t0, len(out), OUT, os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    sys.exit(main())
