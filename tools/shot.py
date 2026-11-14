"""无依赖 CDP 截图：起一个 headless Edge，连上去按时间轴抓几张图 + 读页面状态。

   python tools/shot.py 22 33 42

参数是「演练开始后第几秒」，每个时刻抓一张 PNG 到 tools/shots/。
不依赖 playwright / selenium / websocket-client，只用标准库手搓 RFC6455。
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BASE = os.environ.get("EEW_BASE") or "http://127.0.0.1:8765"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
PORT = int(os.environ.get("EEW_CDP") or 9333)


def http_json(path, data=None):
    req = urllib.request.Request(BASE + path)
    if data is not None:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.load(op.open(req, timeout=6))


def ws_open(url):
    u = urlparse(url)
    s = socket.create_connection((u.hostname, u.port), timeout=15)
    key = base64.b64encode(os.urandom(16)).decode()
    path = u.path + (("?" + u.query) if u.query else "")
    s.sendall((
        "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n" % (path, u.hostname, u.port, key)
    ).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise EOFError("handshake failed")
        buf += chunk
    return s


def ws_send(s, obj):
    data = json.dumps(obj).encode()
    n = len(data)
    hdr = bytearray([0x81])
    if n < 126:
        hdr.append(0x80 | n)
    elif n < 65536:
        hdr.append(0x80 | 126)
        hdr += struct.pack(">H", n)
    else:
        hdr.append(0x80 | 127)
        hdr += struct.pack(">Q", n)
    mask = os.urandom(4)
    hdr += mask
    s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def _read(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise EOFError
        b += c
    return b


def ws_recv(s):
    while True:
        h = _read(s, 2)
        op = h[0] & 0x0F
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _read(s, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _read(s, 8))[0]
        masked = h[1] & 0x80
        mask = _read(s, 4) if masked else None
        payload = _read(s, ln)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if op == 8:
            raise EOFError
        if op == 1:
            return json.loads(payload)


def main():
    times = [float(a) for a in sys.argv[1:]] or [22, 33]
    os.makedirs(OUT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="eewshot")
    proc = subprocess.Popen([
        EDGE, "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        # 这台机器上 --headless 一截图就被 SIGTERM，只能用带窗口的实例，
        # 把窗口挪到屏幕外；同时必须关掉可见性节流，否则 rAF 主循环停摆。
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
        for _ in range(50):
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
        # 走应用自己的「演练」入口，和用户点按钮同一条路
        http_json("/api/sim", {"country": "JP", "mag": 7.2, "depth": 60,
                               "lat": 38.3, "lon": 142.4, "place": "宮城県沖"})
        t0 = time.time()
        for t in times:
            time.sleep(max(0.0, t - (time.time() - t0)))
            probe = call("Runtime.evaluate", {
                "expression": "JSON.stringify({hero:!document.querySelector('#hero').hidden,"
                              "quiet:!document.querySelector('#quiet').hidden,"
                              "obs:!document.querySelector('#obsCard').hidden,"
                              "obsOn:document.body.classList.contains('obs-on'),"
                              "tsu:!!(window.__tsu&&window.__tsu()),"
                              "simT:(window.__eew&&window.__eew.sim&&window.__eew.sim.sim_t)||0,"
                              "serial:(window.__eew&&window.__eew.sim&&window.__eew.sim.serial)||0,"
                              "sobj:!!(window.__eew&&window.__eew.obs),"
                              "slab:(window.__eew&&window.__eew.obs&&window.__eew.obs.max_label)||'',"
                              "stxt:document.querySelector('#obsMax').textContent,"
                              "tsuN:(window.__tsu&&window.__tsu()||{}).regions||0,"
                              "tsuTop:((window.__tsu&&window.__tsu()||{}).top||[]).slice(0,3),"
                              "err:window.__lastErr||'',"
                              "disp:(function(){try{return !!window.__dispEv()}catch(e){return 'err:'+e.message}})()})",
                "returnByValue": True})
            shot = call("Page.captureScreenshot", {"format": "png"})
            name = os.path.join(OUT, "t%02d.png" % int(t))
            with open(name, "wb") as f:
                f.write(base64.b64decode(shot["data"]))
            print("t=%2ds %s -> %s" % (t, probe.get("result", {}).get("value"), name))
            # 再拍一张日本东海岸的放大图：海啸岸线只有几像素宽，全图看不清
            call("Runtime.evaluate", {"expression": "window.__goto(38.0,142.0,2.6)"})
            time.sleep(1.2)
            zoom = call("Page.captureScreenshot", {"format": "png"})
            zname = os.path.join(OUT, "t%02d_zoom.png" % int(t))
            with open(zname, "wb") as f:
                f.write(base64.b64decode(zoom["data"]))
            print("      zoom -> %s" % zname)
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
