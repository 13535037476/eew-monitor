"""静态截图：不跑演练，直接抓实时画面 + 读页面内部状态。

   python tools/shot_plain.py            # 默认视野一张
   python tools/shot_plain.py world      # 再抓一张全球视野
   python tools/shot_plain.py 8770 src   # 换端口，并打开设置里的「数据源」表

复用 shot.py 里手搓的 CDP 客户端；Edge 必须带窗口跑（headless 会被 SIGTERM），
并且关掉可见性节流，否则 rAF 主循环停摆。
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
from shot import EDGE, BASE, OUT, PORT, ws_open, ws_send, ws_recv  # noqa: E402


PROBE = """JSON.stringify({
  gqAct:(window.__eew&&window.__eew.gqAct||[]).length,
  gqSt:(window.__eew&&window.__eew.gqSt||[]).length,
  gqIdx:(window.__eew&&window.__eew.gqIdx&&window.__eew.gqIdx.size)||0,
  zoom:(window.__view?+(window.__view.s/window.__view.fit).toFixed(2):0),
  focus:(window.__eew&&window.__eew.focus)||null,
  liveMax:(document.querySelector('#liveMax')||{}).textContent||'',
  err:window.__lastErr||''
})"""


def main():
    port = next((int(a) for a in sys.argv[1:] if a.isdigit()), 0)
    base = "http://127.0.0.1:%d/" % port if port else BASE
    args = [a for a in sys.argv[1:] if not a.isdigit()]
    os.makedirs(OUT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="eews")
    proc = subprocess.Popen([
        EDGE, "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=%d" % PORT, "--user-data-dir=" + profile,
        "--window-size=1680,1000", base + "/",
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

        def shot(name):
            r = call("Page.captureScreenshot", {"format": "png"})
            p = os.path.join(OUT, name)
            with open(p, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            print("  ->", p)
            return p

        def probe():
            r = call("Runtime.evaluate", {"expression": PROBE, "returnByValue": True})
            return r.get("result", {}).get("value")

        call("Page.enable")
        call("Runtime.enable")
        time.sleep(9)
        print("default:", probe())
        shot("plain_default.png")

        if "src" in args:
            call("Runtime.evaluate", {"expression":
                 "document.querySelector('#btnSettings').click()"})
            time.sleep(1.0)
            call("Runtime.evaluate", {"expression":
                 "document.querySelector('#srcTable').closest('.sec')"
                 ".scrollIntoView({block:'center'})"})
            time.sleep(0.8)
            shot("plain_src.png")

        if "src" in args:
            # 切到备用机后再看一眼：确认「备」芯片高亮、状态正常
            call("Runtime.evaluate", {"expression":
                 "fetch('/api/gq/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({idx:1})})"})
            time.sleep(4.0)
            r = call("Runtime.evaluate", {"expression":
                "JSON.stringify({chips:[].map.call(document.querySelectorAll('[data-gqswap]'),"
                "el=>({i:el.getAttribute('data-gqswap'),on:el.classList.contains('on'),t:el.textContent})),"
                "auto:(document.querySelector('[data-gqauto]')||{classList:{contains:()=>null}}).classList.contains('on')})",
                "returnByValue": True})
            print("alt chips:", r.get("result", {}).get("value"))
            shot("plain_src_alt.png")

        if "world" in args:
            call("Runtime.evaluate", {"expression": "window.__goto(20,110,0.42)"})
            time.sleep(2.2)
            print("world:", probe())
            shot("plain_world.png")

            call("Runtime.evaluate", {"expression": "window.__goto(36,118,3.2)"})
            time.sleep(1.6)
            print("cn:", probe())
            shot("plain_cn.png")
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
