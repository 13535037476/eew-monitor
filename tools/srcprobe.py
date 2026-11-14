"""打开设置面板，把数据源表读出来（顺便截图），验证主源标记。

  python tools/srcprobe.py
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
    profile = tempfile.mkdtemp(prefix="eewsrc")
    proc = subprocess.Popen([
        shot.EDGE, "--disable-gpu", "--no-first-run",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=9336", "--user-data-dir=" + profile,
        "--window-size=1680,1000", shot.BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        target = None
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            time.sleep(0.3)
            try:
                pages = json.load(op.open("http://127.0.0.1:9336/json", timeout=3))
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
        time.sleep(5)
        call("Runtime.evaluate", {"expression": "document.querySelector('#btnSettings').click()"})
        time.sleep(1.2)
        r = call("Runtime.evaluate", {
            "expression": "JSON.stringify({sheet:!document.querySelector('#sheet').hidden,"
                          "rows:[...document.querySelectorAll('#srcTable tr')].map(tr=>tr.innerText.replace(/\\s+/g,' ')),"
                          "primary:JSON.stringify(window.__eew.primary)})",
            "returnByValue": True})
        print(r.get("result", {}).get("value"))
        png = call("Page.captureScreenshot", {"format": "png"})
        out = os.path.join(shot.OUT, "srcpanel.png")
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
