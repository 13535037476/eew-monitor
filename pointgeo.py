"""把气象厅电文里的观测点地点名解析成经纬度。

P2PQuake 中继的震度电文只给地点名（如「球磨村渡」= 市町村 + 観測点名），没有坐标。
这里用 HeartRails Geo API 的市町村 / 町丁目数据反查：

  1. 取该都道府县的市町村列表，按「去掉郡名后的市町村名」做最长前缀匹配
     （「球磨村渡」→ 市町村「球磨郡球磨村」）
  2. 取该市町村的町丁目列表，用剩下的「渡」再做最长前缀匹配拿坐标

命中的地点写进 data/point_geo.json，之后同样的地点直接读缓存、不再联网。
精度是町丁目级别（几公里以内），比按都道府县代表点瞎猜好得多。
"""

import json
import os
import threading
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE = "https://geoapi.heartrails.com/api/json?method="


class PointGeo:
    def __init__(self, cache_path, places_path=None):
        self.path = cache_path
        self.lock = threading.Lock()
        self.data = {"addr": {}, "cities": {}, "towns": {}}
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k in ("addr", "cities", "towns"):
                if isinstance(d.get(k), dict):
                    self.data[k] = d[k]
        except Exception:
            pass
        # 离线市町村表（随 exe 发布）：不用联网就能把电文里的地点名落到坐标上
        self.places = {}
        if places_path is None:
            places_path = os.path.join(os.path.dirname(cache_path), "jp_places.json")
        self.places_path = places_path
        try:
            with open(places_path, "r", encoding="utf-8") as f:
                for pref, rows in (json.load(f).get("prefs") or {}).items():
                    self.places[pref] = [r for r in rows if len(r) >= 3]
        except Exception:
            self.places = {}

    # ---------- 持久化 ----------
    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ---------- HeartRails ----------
    def _api(self, qs):
        req = urllib.request.Request(BASE + qs, headers={
            "User-Agent": UA, "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())

    def _cities(self, pref):
        c = self.data["cities"].get(pref)
        if c is not None:
            return c
        try:
            d = self._api("getCities&prefecture=" + urllib.parse.quote(pref))
            c = [x.get("city") or "" for x in d["response"]["location"]]
        except Exception:
            c = []
        if c:
            self.data["cities"][pref] = c
        return c

    def _towns(self, pref, city):
        key = pref + "|" + city
        t = self.data["towns"].get(key)
        if t is not None:
            return t
        try:
            d = self._api("getTowns&prefecture=%s&city=%s"
                          % (urllib.parse.quote(pref), urllib.parse.quote(city)))
            t = [[x.get("town") or "", float(x.get("y")), float(x.get("x"))]
                 for x in d["response"]["location"] if x.get("x") and x.get("y")]
        except Exception:
            t = []
        if t:                                    # 失败不缓存，下次还能重试
            self.data["towns"][key] = t
        return t

    # ---------- 对外 ----------
    def cached(self, addr):
        return self.data["addr"].get(addr)

    def _offline(self, addr, pref):
        """离线表匹配：都道府县 → 市町村名做最长前缀匹配。

        1) 「球磨村渡」→「球磨郡球磨村」（去郡名后做前缀）
        2) 气象厅地点名常省掉「市」：「大阪北区大淀中」→「大阪市北区」
           「札幌中央区北2条」→「札幌市中央区」（市 + 区 两段都要对得上）
        3) 東京都的区名前面不带市：「東京千代田区大手町」→ 包含「千代田区」
        """
        rows = self.places.get(pref)
        if not rows or not addr:
            return None
        a = addr.strip()
        for name, la, lo in rows:
            short = name.split("郡")[-1]
            if a.startswith(name) or (short and a.startswith(short)):
                return [la, lo]
        for name, la, lo in rows:
            i = name.find("市")
            if i <= 0:
                continue
            head, tail = name[:i], name[i + 1:]
            if a.startswith(head) and (not tail or a[len(head):].startswith(tail)):
                return [la, lo]
        for name, la, lo in rows:
            if len(name) >= 2 and name in a:
                return [la, lo]
        return None

    def lookup(self, addr, pref):
        """addr=「球磨村渡」pref=「熊本県」→ [lat, lon] 或 None"""
        if not addr or not pref:
            return None
        hit = self.data["addr"].get(addr)
        if hit:
            return hit

        # 先走离线表（瞬时、不联网），命中就缓存
        hit = self._offline(addr, pref)
        if hit:
            self.data["addr"][addr] = hit
            return hit

        cities = self._cities(pref)
        best = None
        for c in cities:
            short = c.split("郡")[-1]            # 球磨郡球磨村 → 球磨村
            if not short or not addr.startswith(short):
                continue
            if best is None or len(short) > len(best[1]):
                best = (c, short)
        if best is None:
            return None
        city, short = best
        rest = addr[len(short):]

        towns = self._towns(pref, city)
        pick = None
        for name, lat, lon in towns:
            if not rest:
                if name == "（その他）":
                    pick = (name, lat, lon)
                    break
                if pick is None:
                    pick = (name, lat, lon)
                continue
            if rest.startswith(name) and (pick is None or len(name) > len(pick[0])):
                pick = (name, lat, lon)
        if pick is None and towns:
            pick = (towns[0][0], towns[0][1], towns[0][2])
        if pick is None:
            return None
        res = [round(pick[1], 5), round(pick[2], 5)]
        self.data["addr"][addr] = res
        return res

    def resolve(self, points, limit=8):
        """给一组 {addr, pref} 补 lat/lon，返回补到的个数。

        先用离线市町村表一次性全部落地（瞬时、必须联网失败也能画），
        剩下真匹配不上的，再拿 limit 个去问在线町丁目接口。
        points 会被就地修改。
        """
        with self.lock:
            n = 0
            for p in points:
                if p.get("lat") is not None:
                    continue
                addr = p.get("addr") or ""
                hit = self._offline(addr, p.get("pref") or "")
                if not hit:
                    continue
                p["lat"], p["lon"] = hit[0], hit[1]
                if addr:
                    self.data["addr"][addr] = hit
                n += 1
            net = 0
            for p in points:
                if p.get("lat") is not None:
                    continue
                if net >= limit:
                    break
                net += 1
                hit = self.lookup(p.get("addr") or "", p.get("pref") or "")
                if hit:
                    p["lat"], p["lon"] = hit[0], hit[1]
                    n += 1
            if n:
                self._save()
            return n

    def coverage(self, points):
        got = sum(1 for p in points if p.get("lat") is not None)
        return got, len(points)
