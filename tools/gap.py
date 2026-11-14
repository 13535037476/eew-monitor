"""间隔截图：同一个页面隔 N 秒各抓一张，用来看「什么点在闪/在变」。

    python tools/gap.py 0001 0021        # 第 1 秒、第 21 秒各一张
    python tools/gap.py 0010 0040 0070   # 三张

配合 EEW_BASE 环境变量换端口。
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
from shot import EDGE, BASE, OUT, PORT, ws_open, ws_send, ws_recv  # noqa

PROBE = """JSON.stringify({
  ver:window.__eew.version||'',
  gq:(window.__eew.gqAct||[]).length,
  sim:!!window.__eew.sim,
  serial:(window.__eew.sim&&window.__eew.sim.serial)||0,
  obs:!!window.__eew.obs,
  obsSim:!!(window.__eew.obs&&window.__eew.obs.sim),
  kmoni:Object.keys((window.__eew.kmoni&&window.__eew.kmoni.lv)||{}).length,
  palert:Object.keys((window.__eew.palert&&window.__eew.palert.vals)||{}).length,
  hotK:Object.values((window.__eew.kmoni&&window.__eew.kmoni.lv)||{}).filter(v=>v>=3).length,
  hotP:Object.values((window.__eew.palert&&window.__eew.palert.vals)||{}).filter(v=>v>=3).length,
  simFin:!!(window.__eew.sim&&window.__eew.sim.final),
  simSp:(window.__eew.sim&&window.__eew.sim.speed)||0,
  simRt:(window.__eew.sim&&window.__eew.sim.report_ts)||0,
  simMax:(window.__eew.sim&&window.__eew.sim.max_intensity)||'',
  obsSim:!!(window.__eew.obs&&window.__eew.obs.sim),
  obsMax:(window.__eew.obs&&window.__eew.obs.max_label)||'',
  obsSrc:(window.__eew.obs&&window.__eew.obs.source_name)||'',
  dbg:window.__dbg||null,
  err:window.__lastErr||''
})"""


def main():
    os.makedirs(OUT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="eewgap")
    proc = subprocess.Popen([
        EDGE, "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=%d" % PORT, "--user-data-dir=" + profile,
        "--window-size=1680,1000", BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        target = None
        for _ in range(60):
            time.sleep(0.3)
            try:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                pages = json.load(op.open("http://127.0.0.1:%d/json" % PORT, timeout=3))
                target = next((p for p in pages if p.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                continue
        if not target:
            print("no cdp target")
            return 1
        ws = ws_open(target["webSocketDebuggerUrl"])
        mid = [0]

        def call(method, params=None):
            mid[0] += 1
            ws_send(ws, {"id": mid[0], "method": method, "params": params or {}})
            while True:
                msg = ws_recv(ws)
                if msg.get("id") == mid[0]:
                    return msg.get("result", {})

        call("Page.enable")
        call("Runtime.enable")
        t0 = time.time()
        for name in sys.argv[1:] or ["0001", "0021"]:
            want = int(name) / 10.0 if len(name) <= 3 else float(name)
            while time.time() - t0 < want:
                time.sleep(0.2)
            r = call("Runtime.evaluate", {"expression": PROBE, "returnByValue": True})
            print("t=%5.1fs" % (time.time() - t0), r.get("result", {}).get("value"))
            p = call("Page.captureScreenshot", {"format": "png"})
            path = os.path.join(OUT, "gap_%s.png" % name)
            with open(path, "wb") as f:
                f.write(base64.b64decode(p["data"]))
            print("  ->", path)
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
