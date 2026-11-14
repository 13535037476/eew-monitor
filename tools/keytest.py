"""验证「空格 = 演练 3× 速」：真按键、真读页面状态，不走接口后门。

  python tools/keytest.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shot  # 复用里面手搓的 CDP 客户端


def main():
    shot.http_json("/api/sim/clear", {})
    shot.http_json("/api/sim/speed", {"speed": 1})
    profile = tempfile.mkdtemp(prefix="eewkey")
    proc = subprocess.Popen([
        shot.EDGE, "--disable-gpu", "--no-first-run",
        # 窗口挪到屏幕外会被 Chrome 判成「不可见」，rAF 直接被掐掉，
        # 主循环（画图 + 速报卡）就停了——测试必须关掉这些节流。
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=9334", "--user-data-dir=" + profile,
        "--window-size=1400,900", shot.BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        target = None
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            time.sleep(0.3)
            try:
                pages = json.load(op.open("http://127.0.0.1:9334/json", timeout=3))
                target = next((p for p in pages if p.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                continue
        if not target:
            print("no target")
            return 1
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
        time.sleep(4)                      # 等页面连上 SSE
        shot.http_json("/api/sim", {"country": "JP", "mag": 7.2, "depth": 60,
                                    "lat": 38.3, "lon": 142.4, "place": "宮城県沖"})
        t0 = time.time()
        # 第 1 秒按一下空格（走 keydown → toggleSimSpeed → /api/sim/speed）。
        # 只发一次：窗口在屏幕外没有焦点，Input.dispatchKeyEvent 到不了页面，
        # 用页面内合成事件更可控；两个都发会 toggle 两次，等于没按。
        time.sleep(1.0)
        call("Runtime.evaluate", {
            "expression": "window.dispatchEvent(new KeyboardEvent('keydown',{key:' ',code:'Space',bubbles:true}))"})
        print("空格已按（t=1.0s）")
        last = None
        while time.time() - t0 < 20:
            r = call("Runtime.evaluate", {
                "expression": "JSON.stringify({sp:window.__eew.simSpeed,"
                              "ser:(window.__eew.sim&&window.__eew.sim.serial)||0,"
                              "fin:!!(window.__eew.sim&&window.__eew.sim.final),"
                              "obs:!!window.__eew.obs,hero:!document.querySelector('#hero').hidden,"
                              "card:!document.querySelector('#obsCard').hidden,"
                              "diag:(function(){var o=window.__eew.obs||{};"
                              "var now=Date.now()/1000+(window.__eew.offset||0);"
                              "return 'age='+Math.round(now-(o.report_ts||0))+' n='+(o.pref?Object.keys(o.pref).length:-1)+' sim='+(!!o.sim)+' off='+(window.__eew.offset||0).toFixed(2)+' cls='+document.body.className;})()"
                              ",err:window.__lastErr||''})",
                "returnByValue": True})
            d = json.loads(r.get("result", {}).get("value") or "{}")
            sig = (d.get("ser"), d.get("fin"), d.get("hero"), d.get("card"), d.get("obs"))
            if sig != last:
                last = sig
                print("%5.1fs 第%s报 final=%s 倍速=%s hero=%s 速报卡=%s 有obs=%s | %s %s" % (
                    time.time() - t0, d.get("ser"), d.get("fin"), d.get("sp"),
                    d.get("hero"), d.get("card"), d.get("obs"), d.get("diag"), d.get("err") or ""))
            time.sleep(0.25)
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
        shot.http_json("/api/sim/speed", {"speed": 1})


if __name__ == "__main__":
    sys.exit(main())
