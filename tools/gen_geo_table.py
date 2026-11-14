# -*- coding: utf-8 -*-
"""生成 data/jp_places.json：日本市町村/行政区 → 经纬度 的离线反查表。

**不产生任何台站**。这张表只干一件事：气象厅震度电文里的观测点只有地点名
（「球磨村渡」= 市町村 + 観測点名），没有坐标；收到电文后要按名字反查坐标，
才能把**电文里实际观测到的那些测站**逐个画到地图上。

数据源：HeartRails Geo API 的 getCities / getTowns（市町村官方名称、町丁目坐标）。
每个市町村取一个代表点（优先「（その他）」，否则第一个町丁目），
结果写进 data/jp_places.json 随 exe 一起发布，运行时无需联网。
"""
import json
import os
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DST = os.path.join(ROOT, "data", "jp_places.json")
BASE = "https://geoapi.heartrails.com/api/json?method="
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

PREFS = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県",
    "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県",
    "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県",
    "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県",
    "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

LOCK = threading.Lock()


def api(qs, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(BASE + qs, headers={
                "User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
            loc = (d.get("response") or {}).get("location")
            return loc or []
        except Exception:
            if i == tries - 1:
                return None
    return None


def cities(pref):
    loc = api("getCities&prefecture=" + urllib.parse.quote(pref))
    if not loc:
        return []
    return [x.get("city") for x in loc if x.get("city")]


def rep_point(pref, city):
    loc = api("getTowns&prefecture=%s&city=%s" %
              (urllib.parse.quote(pref), urllib.parse.quote(city)))
    if not loc:
        return None
    other = None
    first = None
    for x in loc:
        try:
            la, lo = float(x["y"]), float(x["x"])
        except (KeyError, TypeError, ValueError):
            continue
        if first is None:
            first = [la, lo]
        if (x.get("town") or "") == "（その他）":
            other = [la, lo]
            break
    return other or first


def main():
    data = {"prefs": {}}
    if os.path.exists(DST):
        try:
            old = json.load(open(DST, encoding="utf-8"))
            if isinstance(old.get("prefs"), dict):
                data = old
        except Exception:
            pass
    done = set()
    for pref, lst in data["prefs"].items():
        for row in lst:
            done.add((pref, row[0]))

    for pref in PREFS:
        cs = cities(pref)
        todo = [c for c in cs if (pref, c) not in done]
        print("%s: %d 市町村, 待补 %d" % (pref, len(cs), len(todo)), flush=True)
        if not todo:
            continue
        with ThreadPoolExecutor(max_workers=8) as ex:
            pts = list(ex.map(lambda c: rep_point(pref, c), todo))
        have = {row[0]: row for row in data["prefs"].get(pref, [])}
        for c, p in zip(todo, pts):
            if p:
                have[c] = [c, round(p[0], 4), round(p[1], 4)]
        data["prefs"][pref] = sorted(have.values(), key=lambda r: -len(r[0]))
        with LOCK:
            os.makedirs(os.path.dirname(DST), exist_ok=True)
            tmp = DST + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, DST)

    n = sum(len(v) for v in data["prefs"].values())
    print("prefs=%d places=%d size=%dKB" %
          (len(data["prefs"]), n, os.path.getsize(DST) // 1024))


if __name__ == "__main__":
    main()
