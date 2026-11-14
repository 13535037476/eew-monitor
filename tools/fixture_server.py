# -*- coding: utf-8 -*-
"""本地假数据服务：用真实气象厅地点名造一份震度电文，验证「测站逐个落点」。

    python tools/fixture_server.py 8798

只是给前端联调用，不参与打包。
"""
import json
import os
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pointgeo  # noqa: E402

GEO = pointgeo.PointGeo(os.path.join(ROOT, "data", "point_geo.json"),
                        os.path.join(ROOT, "data", "jp_places.json"))

# (地点名, 都道府县, 震度) —— 都是气象厅震度观测点的真实叫法
RAW = [
    ("球磨村渡", "熊本県", 55), ("熊本中央区", "熊本県", 55), ("八代市平山新町", "熊本県", 50),
    ("人吉市下新町", "熊本県", 50), ("山鹿市菊鹿町", "熊本県", 45), ("菊池市隈府", "熊本県", 45),
    ("宇土市浦田", "熊本県", 45), ("宇城市不知火町", "熊本県", 40), ("阿蘇市一の宮町", "熊本県", 40),
    ("合志市竹迫", "熊本県", 40), ("上天草市大矢野町", "熊本県", 40), ("天草市今釜新町", "熊本県", 30),
    ("大分市府内町", "大分県", 40), ("別府市天間", "大分県", 30), ("佐伯市大手町", "大分県", 30),
    ("宮崎市和知川原", "宮崎県", 30), ("鹿児島市東郡元町", "鹿児島県", 30),
    ("福岡市中央区大濠", "福岡県", 30), ("長崎市古賀町", "長崎県", 20), ("佐賀市駅前中央", "佐賀県", 20),
    ("山口市亀山", "山口県", 20), ("広島市中区上八丁堀", "広島県", 20), ("松江市西津田", "島根県", 20),
    ("鳥取市吉方", "鳥取県", 10), ("高知市本町", "高知県", 20), ("徳島市新蔵町", "徳島県", 10),
    ("松山市北条", "愛媛県", 10), ("高松市伏石町", "香川県", 10), ("岡山市北区大供", "岡山県", 10),
    ("福山市南本郷町", "広島県", 10), ("大阪北区大淀中", "大阪府", 10), ("神戸中央区脇浜海岸通", "兵庫県", 10),
    ("京都中京区西ノ京笠殿町", "京都府", 10), ("奈良市法蓮町", "奈良県", 10),
    ("和歌山市茶屋ノ丁", "和歌山県", 10), ("名古屋中区三の丸", "愛知県", 10),
    ("静岡市駿河区曲金", "静岡県", 10), ("甲府市飯田", "山梨県", 10), ("長野市箱清水", "長野県", 10),
    ("金沢市西念", "石川県", 10), ("富山市石坂", "富山県", 10), ("福井市豊島", "福井県", 10),
    ("岐阜市加納二之丸", "岐阜県", 10), ("津市島崎町", "三重県", 10), ("大津市打出浜", "滋賀県", 10),
    ("姫路市本町", "兵庫県", 10), ("東京千代田区大手町", "東京都", 10), ("横浜中区山手町", "神奈川県", 10),
    ("さいたま市中央区新都心", "埼玉県", 10), ("千葉市中央区中央港", "千葉県", 10),
    ("水戸市金町", "茨城県", 10), ("宇都宮市明保野町", "栃木県", 10), ("前橋市昭和町", "群馬県", 10),
    ("新潟市中央区美咲町", "新潟県", 10), ("仙台市宮城野区五輪", "宮城県", 10),
    ("福島市松木町", "福島県", 10), ("郡山市朝日", "福島県", 10), ("盛岡市山王町", "岩手県", 10),
    ("青森市花園", "青森県", 10), ("秋田市山王", "秋田県", 10), ("山形市緑町", "山形県", 10),
    ("札幌市中央区北2条", "北海道", 10), ("函館市美原", "北海道", 10), ("旭川市8条通", "北海道", 10),
    ("釧路市幣舞町", "北海道", 10), ("帯広市東4条", "北海道", 10), ("室蘭市山手町", "北海道", 10),
    ("那覇市樋川", "沖縄県", 10), ("石垣市登野城", "沖縄県", 10), ("宮古島市平良", "沖縄県", 10),
]

LABEL = {10: "1", 20: "2", 30: "3", 40: "4", 45: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7"}


def build_obs():
    pts = [{"addr": a, "pref": p, "scale": s, "label": LABEL.get(s, "?")}
           for a, p, s in RAW]
    n = GEO.resolve(pts, limit=0)
    print("坐标命中 %d/%d" % (n, len(pts)), flush=True)
    for p in pts:
        if p.get("lat") is None:
            print("  未命中:", p["pref"], p["addr"], flush=True)
    now = time.time()
    return {
        "kind": "obs", "source": "jma_obs", "source_name": "気象庁 震度観測点",
        "origin_ts": now - 900, "report_ts": now - 800,
        "place": "熊本県熊本地方", "lat": 32.75, "lon": 130.76, "depth": 10.0, "mag": 6.2,
        "max_scale": 55, "max_label": "6弱", "point_count": len(pts),
        "points": sorted(pts, key=lambda p: -p["scale"]),
        "pref": {}, "scales": {},
    }


OBS = build_obs()


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=os.path.join(ROOT, "web"), **kw)

    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            return self._json({
                "ts": time.time(), "version": "1.7.9-test", "uptime": 60,
                "sources": [], "eew": {"JP": None, "CN": None},
                "events": [], "alerts": [], "sim": None, "obs": OBS,
            })
        if self.path.startswith("/api/stations"):
            import stations
            return self._json({"stations": stations.STATIONS,
                               "builtin": len(stations.STATIONS)})
        if self.path.startswith("/api/sound"):
            return self._json({"ok": True})
        if self.path.startswith("/api/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            try:
                for _ in range(600):
                    self.wfile.write(b"data: " + json.dumps({
                        "ts": time.time(), "version": "1.7.9-test", "uptime": 60,
                        "sources": [], "eew": {"JP": None, "CN": None},
                        "events": [], "alerts": [], "sim": None, "obs": OBS,
                    }, ensure_ascii=False).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                    time.sleep(2)
            except Exception:
                pass
            return
        return super().do_GET()

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        if ln:
            self.rfile.read(ln)
        return self._json({"ok": True})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8798
    print("fixture on http://127.0.0.1:%d/" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
