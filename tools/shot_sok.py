"""截一张「震度速報」闪现卡的图，验证 JMA 风格渲染。

    python tools/shot_sok.py [eggy 密码 模式]

模式：調査中（默认，震度 3）/ 已知 / 1 / 2 / 4 / 5弱 / 5強 / 6弱 / 6強 / 7 / ？

流程：登录 API 拿 session → Edge 带 cookie 打开首页 → 调 window.__sokTest 喂假报 → 截图
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

BASE = os.environ.get("EEW_BASE") or "http://127.0.0.1:8765"
PORT = int(os.environ.get("EEW_CDP") or 9342)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
USER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EEW_USER", "")
PWD = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("EEW_PWD", "")
if not USER or not PWD:
    sys.exit("用法：python tools/shot_sok.py <账号> <密码>，或设 EEW_USER / EEW_PWD 环境变量")

MODE = sys.argv[3] if len(sys.argv) > 3 else "調査中"


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
    raise SystemExit("登录失败")


def main():
    os.makedirs(OUT, exist_ok=True)
    token = login()
    profile = tempfile.mkdtemp(prefix="eewsok")
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

        def js(expr):
            r = call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            return r.get("result", {}).get("value")

        call("Page.enable")
        call("Runtime.enable")
        call("Network.enable")
        call("Network.setCookie", {"name": "eew_session", "value": token,
                                   "domain": "127.0.0.1", "path": "/"})
        call("Page.navigate", {"url": BASE + "/"})
        # app.js 有 240KB，还要等状态接口；脚本没就位就喂假报会静默失败
        for _ in range(60):
            time.sleep(0.5)
            if js("typeof window.__sokTest") == "function":
                break
        time.sleep(1.0)
        import time as _t
        now = _t.time()
        if MODE == "已知":
            ev = {"uid": "TEST-KNOWN", "country": "JP", "origin_ts": now - 3,
                  "report_ts": now - 2, "place": "茨城県北部", "mag": 5.1,
                  "depth": 40, "max_intensity": "5弱", "source": "jma_eew"}
        elif MODE in ("1", "2", "4", "5弱", "5強", "6弱", "6強", "7", "?"):
            ev = {"uid": "TEST-COLOR", "country": "JP", "origin_ts": now - 4,
                  "report_ts": now - 3, "max_intensity": MODE, "source": "jma_eew"}
        else:
            ev = {"uid": "TEST-SOKUHO", "country": "JP", "origin_ts": now - 4,
                  "report_ts": now - 3, "max_intensity": "3", "source": "jma_eew"}
        # 先验「只有气象厅官报才弹」：别的来源必须为 false
        print("isJma:", js(
            "JSON.stringify({jma_eew:__sokIsJma({source:'jma_eew'}),"
            "jma_sokuho:__sokIsJma({source:'jma_sokuho'}),"
            "cn_eew:__sokIsJma({source:'cn_eew'}),cwa:__sokIsJma({source:'cwa_eew'}),"
            "sim:__sokIsJma({source:'sim'}),none:__sokIsJma({})})"))
        r = call("Runtime.evaluate",
                 {"expression": "window.__sokTest(%s)" % json.dumps(ev, ensure_ascii=False),
                  "returnByValue": True})
        if r.get("exceptionDetails"):
            print("EXC:", json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
        time.sleep(1.2)
        print("probe:", js(
            "JSON.stringify({shown:!document.querySelector('#sokuhoFlash').hidden,"
            "int:document.querySelector('#sokInt').textContent,"
            "place:document.querySelector('#sokPlace').textContent,"
            "time:document.querySelector('#sokTime').textContent,"
            "strip:document.querySelector('#sokStrip').textContent,"
            "small:document.querySelector('.sok-int').classList.contains('small'),"
            "bg:getComputedStyle(document.querySelector('.sok-card')).backgroundColor,"
            "fg:getComputedStyle(document.querySelector('#sokInt')).color})"))
        png = call("Page.captureScreenshot", {"format": "png"})
        tag = {"調査中": "chosa", "已知": "known"}.get(MODE, "c" + str(MODE).replace("弱", "w").replace("強", "s").replace("?", "q"))
        path = os.path.join(OUT, "sokuho_%s.png" % tag)
        with open(path, "wb") as f:
            f.write(base64.b64decode(png["data"]))
        print("saved", path)
        # 点一下应当收起
        js("document.querySelector('#sokuhoFlash').click()")
        time.sleep(0.6)
        print("after click hidden:", js("document.querySelector('#sokuhoFlash').hidden"))
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
