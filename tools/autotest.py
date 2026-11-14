"""验证「本地推算震源」：往 kmoni 的测站表里注入一撮震动，看能不能算出震源。

  python tools/autotest.py
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
    profile = tempfile.mkdtemp(prefix="eewauto")
    proc = subprocess.Popen([
        shot.EDGE, "--disable-gpu", "--no-first-run",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=9338", "--user-data-dir=" + profile,
        "--window-size=1400,900", shot.BASE + "/",
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

        call("Runtime.enable")
        time.sleep(6)
        n_st = call("Runtime.evaluate", {
            "expression": "String((window.__eew.stations||[]).filter(s=>s.country==='JP'&&s.region==='宮城県').length)",
            "returnByValue": True})
        print("宮城県 stations:", n_st.get("result", {}).get("value"))

        # 注入一撮：宮城県的前 12 个站，4 个绿色档、其余 1.4
        call("Runtime.evaluate", {"expression":
            "(function(){var s=window.__eew;"
            "var all=s.stations.filter(function(x){return x.country==='JP';});"
            "var seed=null; for(var i=0;i<all.length;i++){ if(all[i].region==='岩手県'){seed=all[i];break;} }"
            "var arr=all.filter(function(x){return seed&&Math.abs(x.lat-seed.lat)<0.55&&Math.abs(x.lon-seed.lon)<0.55;}).slice(0,18);"
            "s.kmoni=s.kmoni||{}; s.kmoni.lv=s.kmoni.lv||{};"
            "arr.forEach(function(x,i){ s.kmoni.lv[x.code]= i<6?2.2:2.05; });"
            "s.kmoni.ok=true; s.kmoni.at=Date.now()/1000; s.kmoni.ts='demo-'+Date.now();"
            "return arr.length;})()"})
        got = call("Runtime.evaluate", {
            "expression": "(function(){var ev=window.__auto();"
                          "window.__eew.autoEv=ev;"
                          "return JSON.stringify(ev);})()",
            "returnByValue": True})
        print("autoHypo ->", got.get("result", {}).get("value"))

        # 冷却期：再注入一批，不该换震源
        got2 = call("Runtime.evaluate", {
            "expression": "(function(){var ev=window.__auto();"
                          "return JSON.stringify(ev&&ev.place||null);})()",
            "returnByValue": True})
        print("冷却期再算 place:", got2.get("result", {}).get("value"))
        return 0
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
