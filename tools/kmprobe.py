"""探查 kmoni 取色链路：window.__km / state.kmoni / 台站数 / 绿框与推算状态。

  python tools/kmprobe.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shot


def main():
    profile = tempfile.mkdtemp(prefix="eewkm")
    proc = subprocess.Popen([
        shot.EDGE, "--disable-gpu", "--no-first-run",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=9337", "--user-data-dir=" + profile,
        "--window-size=1680,1000", shot.BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        target = None
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            time.sleep(0.3)
            try:
                pages = json.load(op.open("http://127.0.0.1:9337/json", timeout=3))
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
            r = call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                          "awaitPromise": True})
            if "exceptionDetails" in r:
                return "EXC " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:300]
            return r.get("result", {}).get("value")

        call("Runtime.enable")
        for t in (4, 8, 12, 16):
            time.sleep(4)
            print("--- t=%ds" % t)
            print("  km window.__km:", js("JSON.stringify(window.__km||null)"))
            print("  km src:", js("JSON.stringify({ts:window.__eew.kmoni.ts,ok:window.__eew.kmoni.ok,"
                                "n:window.__eew.kmoni.n,len:Object.keys(window.__eew.kmoni.lv||{}).length,"
                                "at:Math.round(window.__eew.kmoni.at||0),"
                                "now:Math.round(Date.now()/1000+(window.__eew.offset||0))})"))
            print("  stations:", js("JSON.stringify({all:window.__eew.stations.length,"
                                   "jp:window.__eew.stations.filter(s=>s.country==='JP').length,"
                                   "withCode:window.__eew.stations.filter(s=>s.country==='JP'&&s.code).length})"))
            print("  shakeN/auto:", js("JSON.stringify({shake:window.__eew.shakeN,"
                                       "auto:!!window.__eew.autoEv,"
                                       "autoPlace:(window.__eew.autoEv||{}).place||null,"
                                       "err:window.__lastErr||''})"))
            print("  topLv:", js("(function(){var lv=window.__eew.kmoni.lv||{};"
                                 "var a=Object.keys(lv).map(k=>lv[k]).sort((x,y)=>y-x).slice(0,8);"
                                 "return JSON.stringify(a);})()"))
            print("  imgmeta:", js("JSON.stringify({w:(window.__kmImgInfo||null)})"))
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
