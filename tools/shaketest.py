"""验证「两个中等蓝台站 → 绿框闪烁」：往 kmoni 注两个震度2的站，截图看框。

  python tools/shaketest.py
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


def main():
    profile = tempfile.mkdtemp(prefix="eewshake")
    proc = subprocess.Popen([
        shot.EDGE, "--disable-gpu", "--no-first-run",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=9339", "--user-data-dir=" + profile,
        "--window-size=1400,900", shot.BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        target = None
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            time.sleep(0.3)
            try:
                pages = json.load(op.open("http://127.0.0.1:9339/json", timeout=3))
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
        call("Runtime.evaluate", {"expression": "window.__goto(38.3,140.9,9)"})
        # 每 120ms 覆盖一次：kmoni 循环每秒会整块替换 state.kmoni，不持续注入会被冲掉
        call("Runtime.evaluate", {"expression":
            "window.__demo=setInterval(function(){var s=window.__eew;"
            "s.kmoni=s.kmoni||{}; s.kmoni.ok=true; s.kmoni.at=Date.now()/1000;"
            "if(!s.kmoni.lv) s.kmoni.lv={};"
            "var a=s.stations.filter(function(x){return x.country==='JP'&&x.region==='宮城県';});"
            "if(a[0]) s.kmoni.lv[a[0].code]=1.9;"
            "if(a[1]) s.kmoni.lv[a[1].code]=2.3;"
            "},120); 'ok'"})
        time.sleep(1.6)
        info = call("Runtime.evaluate", {
            "expression": "JSON.stringify({shake:window.__eew.shakeN,"
                          "auto:!!window.__eew.autoEv})",
            "returnByValue": True})
        print(info.get("result", {}).get("value"))
        png = call("Page.captureScreenshot", {"format": "png"})
        out = os.path.join(shot.OUT, "shake.png")
        with open(out, "wb") as f:
            f.write(base64.b64decode(png["data"]))
        print("shot:", out)
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
