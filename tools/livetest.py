"""端到端：注入一片岩手县摇晃，验证绿框 + 自动推算是否真的会自然触发。

  python tools/livetest.py [峰值计测震度]
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shot

LEVEL = float(sys.argv[1]) if len(sys.argv) > 1 else 1.8


def main():
    profile = tempfile.mkdtemp(prefix="eewlive")
    proc = subprocess.Popen([
        shot.EDGE, "--disable-gpu", "--no-first-run", "--hide-scrollbars",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=9338", "--user-data-dir=" + profile,
        "--window-size=1680,1000", shot.BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        target = None
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            time.sleep(0.3)
            try:
                pages = json.load(op.open("http://127.0.0.1:9338/json", timeout=3))
                target = next((p for p in pages if p.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                continue
        ws = shot.ws_open(target["webSocketDebuggerUrl"])
        mid = [0]

        def call(method, params=None):
            mid[0] += 1
            shot.ws_send(ws, {"id": mid[0], "method": method, "params": params or {}})
            while True:
                msg = shot.ws_recv(ws)
                if msg.get("id") == mid[0]:
                    return msg.get("result", {})

        def js(expr):
            r = call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            if "exceptionDetails" in r:
                return "EXC " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:200]
            return r.get("result", {}).get("value")

        call("Runtime.enable")
        time.sleep(6)

        # 真实 kmoni 循环每 350ms 覆盖一次 state.kmoni，注入一次撑不住，
        # 所以每 120ms 把假数据写回去（挡请求反而更糟：catch 里会把 ok 置 false）
        inject = """
        (function(){
          var S = window.__eew;
          var all = S.stations.filter(function(x){return x.country==='JP';});
          var seed = null;
          for (var i=0;i<all.length;i++){ if(all[i].region==='岩手県' && all[i].lat>39.0){ seed=all[i]; break; } }
          if(!seed) return 'no seed';
          var lv = {};
          var near = all.filter(function(x){
            return Math.hypot((x.lat-seed.lat)*111,(x.lon-seed.lon)*86) < 55;
          }).slice(0, 40);
          near.forEach(function(x){
            var d = Math.hypot((x.lat-seed.lat)*111,(x.lon-seed.lon)*86);
            lv[x.code] = Math.max(0.6, %f - d*0.02);
          });
          var inj = { ok:true, at: Date.now()/1000, ts:'TEST'+Date.now(), lv: lv, n: Object.keys(lv).length };
          S.kmoni = inj; S.autoEv = null;
          if (window.__injT) clearInterval(window.__injT);
          window.__injT = setInterval(function(){ inj.at = Date.now()/1000; S.kmoni = inj; }, 120);
          return JSON.stringify({seed: seed.name, n: Object.keys(lv).length,
            top: Math.max.apply(null, Object.values(lv))});
        })()
        """ % LEVEL
        print("inject:", js(inject))

        for i in range(6):
            time.sleep(1)
            print(" t+%ds" % (i + 1), js(
                "JSON.stringify({shake:window.__eew.shakeN,"
                "auto:(window.__eew.autoEv||{}).place||null,"
                "mag:(window.__eew.autoEv||{}).mag||null,"
                "st:(window.__eew.autoEv||{}).stations||null,"
                "trig:(window.__eew.autoEv||{}).trigger||null,"
                "rms:(window.__eew.autoEv||{}).rms||null,"
                "net:(document.querySelector('#lmNetPlace')||{}).textContent||''})"))

        shot_name = os.path.join(shot.OUT, "live.png")
        img = call("Page.captureScreenshot", {"format": "png"})
        with open(shot_name, "wb") as f:
            f.write(base64.b64decode(img["data"]))
        print("shot ->", shot_name)

        pt = js("(function(){var e=window.__eew.autoEv||{};"
                "if(e.lat===undefined) return null;"
                "var p=window.__screenOf(e.lat,e.lon);"
                "return JSON.stringify(p);})()")
        if pt:
            x, y = json.loads(pt)
            clip = {"x": max(0, x - 150), "y": max(0, y - 110),
                    "width": 300, "height": 220, "scale": 3}
            z = call("Page.captureScreenshot", {"format": "png", "clip": clip})
            zname = os.path.join(shot.OUT, "live_zoom.png")
            with open(zname, "wb") as f:
                f.write(base64.b64decode(z["data"]))
            print("zoom ->", zname, "at", x, y)
    finally:
        if ws is not None:
            try:
                shot.ws_close(ws)
            except Exception:
                pass
        try:
            proc.terminate()
        except Exception:
            pass


main()
