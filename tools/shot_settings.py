"""截一张「设置面板 · 我的 Jian 密钥」的图，验证前端入口。

    python tools/shot_settings.py [账号] [密码]

沿用 shot.py 的 CDP 封装（无第三方依赖），流程：
    登录 API 拿 session → Edge 带 cookie 打开首页 → 点开设置 → 截图
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
import shot  # noqa: E402

BASE = os.environ.get("EEW_BASE") or "http://127.0.0.1:8899"
PORT = int(os.environ.get("EEW_CDP") or 9334)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
USER = sys.argv[1] if len(sys.argv) > 1 else "eewtest"
PWD = sys.argv[2] if len(sys.argv) > 2 else "REDACTED"


def login():
    req = urllib.request.Request(
        BASE + "/api/login",
        data=json.dumps({"username": USER, "password": PWD}).encode(),
        headers={"Content-Type": "application/json"},
    )
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=8) as r:
        cookie = r.headers.get("Set-Cookie") or ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "eew_session":
            return v
    raise SystemExit("登录失败，没拿到 eew_session")


def main():
    os.makedirs(OUT, exist_ok=True)
    token = login()
    profile = tempfile.mkdtemp(prefix="eewset")
    proc = subprocess.Popen([
        shot.EDGE, "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-position=-2200,-2200",
        "--remote-debugging-port=%d" % PORT, "--user-data-dir=" + profile,
        "--window-size=1680,1000", "about:blank",
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
        ws = shot.ws_open(target["webSocketDebuggerUrl"])
        mid = [0]

        def call(method, params=None):
            mid[0] += 1
            shot.ws_send(ws, {"id": mid[0], "method": method, "params": params or {}})
            while True:
                msg = shot.ws_recv(ws)
                if msg.get("id") == mid[0]:
                    return msg.get("result", {})

        call("Page.enable")
        call("Runtime.enable")
        call("Network.enable")
        call("Network.setCookie", {"name": "eew_session", "value": token,
                                   "domain": "127.0.0.1", "path": "/"})
        call("Page.navigate", {"url": BASE + "/"})
        time.sleep(6)
        call("Runtime.evaluate", {
            "expression": "document.querySelector('#btnSettings').click()"})
        time.sleep(1.5)
        # 滚到「数据源」那一块，让密钥输入框落在视口里
        call("Runtime.evaluate", {
            "expression": "document.querySelector('.mytok').scrollIntoView({block:'center'})"})
        time.sleep(0.6)
        probe = call("Runtime.evaluate", {
            "expression": "JSON.stringify({state:document.querySelector('#myTokState').textContent,"
                          "ph:document.querySelector('#myTokInput').placeholder,"
                          "hasSave:!!document.querySelector('#btnMyTokSave')})",
            "returnByValue": True})
        print("probe:", probe.get("result", {}).get("value"))
        shot_png = call("Page.captureScreenshot", {"format": "png"})
        path = os.path.join(OUT, "settings_tokens.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(shot_png["data"]))
        print("saved", path)
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
