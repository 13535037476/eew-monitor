"""演练时间轴截图：3× 倍速跑一场，几个关键时刻各抓一张 + 打印每报的解。

    python tools/simshot.py            # 默认 3× 跑 JP M7.2
    python tools/simshot.py 8 20 30    # 指定墙上秒数

看的是：第一报之前有没有「台站检测中」、每报位置/深度/震级有没有变化、
震度速報是不是在终报之后才出。
"""
import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shot import EDGE, BASE, OUT, PORT, http_json, ws_open, ws_send, ws_recv  # noqa
import subprocess  # noqa: E402
import tempfile     # noqa: E402


PROBE = """JSON.stringify({
  t:(window.__eew&&window.__eew.sim&&window.__eew.sim.sim_t)||0,
  serial:(window.__eew&&window.__eew.sim&&window.__eew.sim.serial)||0,
  mag:(window.__eew&&window.__eew.sim&&window.__eew.sim.mag)||0,
  dep:(window.__eew&&window.__eew.sim&&window.__eew.sim.depth)||0,
  lat:(window.__eew&&window.__eew.sim&&window.__eew.sim.lat)||0,
  lon:(window.__eew&&window.__eew.sim&&window.__eew.sim.lon)||0,
  det:(window.__eew&&window.__eew.sim&&window.__eew.sim.sim_det||[]).length,
  fin:!!(window.__eew&&window.__eew.sim&&window.__eew.sim.final),
  pending:!!(window.__eew&&window.__eew.sim&&window.__eew.sim.pending),
  hero:!document.querySelector('#hero').hidden,
  obs:!document.querySelector('#obsCard').hidden,
  quiet:(document.querySelector('#quietTxt')||{}).textContent||'',
  err:window.__lastErr||''
})"""


def main():
    times = [float(a) for a in sys.argv[1:]] or [4, 14, 24, 32, 42, 52]
    os.makedirs(OUT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="simshot")
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
        http_json("/api/sim/speed", {"speed": 3})
        http_json("/api/sim", {"country": "JP", "mag": 7.2, "depth": 60,
                               "lat": 38.3, "lon": 142.4, "place": "宮城県沖"})
        t0 = time.time()
        for t in times:
            time.sleep(max(0.0, t - (time.time() - t0)))
            probe = call("Runtime.evaluate", {"expression": PROBE, "returnByValue": True})
            shot = call("Page.captureScreenshot", {"format": "png"})
            name = os.path.join(OUT, "sim%02d.png" % int(t))
            with open(name, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            print("t=%2ds %s" % (t, probe.get("result", {}).get("value")))
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
