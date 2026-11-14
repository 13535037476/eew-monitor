"""看国家名标注：复位视野 / 全球视野各截一张。

  python tools/maplabels.py
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
    profile = tempfile.mkdtemp(prefix="eewlbl")
    proc = subprocess.Popen([
        shot.EDGE, "--disable-gpu", "--no-first-run", "--hide-scrollbars",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=9339", "--user-data-dir=" + profile,
        "--window-size=1680,1000", shot.BASE + "/",
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

        def js(expr):
            r = call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            return r.get("result", {}).get("value")

        def snap(name):
            img = call("Page.captureScreenshot", {"format": "png"})
            p = os.path.join(shot.OUT, name)
            with open(p, "wb") as f:
                f.write(base64.b64decode(img["data"]))
            print("->", p)

        call("Runtime.enable")
        time.sleep(6)
        # 关掉可能的演练/事件聚焦，复位到默认「东亚」视野
        js("document.querySelector('[data-act=\"home\"]').click()")
        time.sleep(1.6)
        print("zoom:", js("(window.__eew.view.s/window.__eew.view.fit).toFixed(2)"))
        snap("labels_home.png")
        print("zoom:", js("String(window.__view.s/window.__view.fit)"))
        print("layers:", js("JSON.stringify(window.__eew.layers)"))
        pt = js("JSON.stringify(window.__screenOf(35.0,103.5))")
        print("cn at", pt)
        if pt:
            x, y = json.loads(pt)
            z = call("Page.captureScreenshot", {"format": "png",
                      "clip": {"x": max(0, x - 200), "y": max(0, y - 120),
                               "width": 400, "height": 240, "scale": 3}})
            with open(os.path.join(shot.OUT, "labels_cn.png"), "wb") as f:
                f.write(base64.b64decode(z["data"]))
            print("-> labels_cn.png")
        js("document.querySelector('[data-act=\"globe\"]').click()")
        time.sleep(1.6)
        print("zoom globe:", js("(window.__eew.view.s/window.__eew.view.fit).toFixed(2)"))
        snap("labels_globe.png")
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
