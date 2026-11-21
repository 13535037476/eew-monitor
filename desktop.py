"""震线 EEW 桌面版

用一个内嵌的 WebView2 窗口承载界面：**不启动外部浏览器**，也不占用系统浏览器进程。
后端仍是 server.py 里的本地 HTTP 服务（只监听 127.0.0.1）。

用法：
    python desktop.py              # 打开桌面窗口
    python desktop.py --selftest   # 启动 4 秒后自动关闭（打包后自检用）
"""
import os
import socket
import sys
import threading
import time
import urllib.request

# 必须在 import server 之前：告诉服务端「桌面模式」，声音走 winsound
os.environ["EEW_DESKTOP"] = "1"
# 息屏录制的前提：WebView2 默认会把「被遮挡/最小化」的窗口降频——定时器掐到
# 一分钟一次、requestAnimationFrame 直接停，录下来的就是一帧静止画面。
# 这几个开关关掉后台节流，配合服务端的「系统不休眠」，人出门也能照录。
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--disable-background-timer-throttling "
    "--disable-backgrounding-occluded-windows "
    "--disable-renderer-backgrounding "
    "--disable-features=CalculateNativeWinOcclusion")

import server


def pick_port(start=8765, tries=30):
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(base, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(base + "api/health", timeout=1.2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.12)
    return False


TITLE = "震线 EEW · 中日地震预警"


def polish_window(title):
    """窗口出来之后：深色标题栏 + 应用图标"""
    try:
        import ctypes
        u = ctypes.windll.user32
    except Exception:
        return
    hwnd = 0
    for _ in range(120):
        hwnd = u.FindWindowW(None, title)
        if hwnd:
            break
        time.sleep(0.08)
    if not hwnd:
        return

    try:
        dwm = ctypes.windll.dwmapi
        v = ctypes.c_int(1)
        for attr in (20, 19):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass

    try:
        ico = os.path.join(server.WEB_DIR, "icon.ico")
        if os.path.exists(ico):
            u.LoadImageW.restype = ctypes.c_void_p
            h = u.LoadImageW(None, ico, 1, 32, 32, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                u.SendMessageW(h, 0x0080, 0, h)   # WM_SETICON, ICON_SMALL
                u.SendMessageW(h, 0x0080, 1, h)   # ICON_BIG
    except Exception:
        pass


def main():
    selftest = "--selftest" in sys.argv
    port = pick_port()
    srv = server.boot("127.0.0.1", port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    base = "http://127.0.0.1:%d/" % port
    if not wait_ready(base):
        print("服务启动失败", file=sys.stderr)
        return 1

    import webview
    window = webview.create_window(
        TITLE,
        base,
        width=1520,
        height=940,
        min_size=(1080, 700),
        background_color="#070d15",
    )

    if selftest:
        def closer():
            time.sleep(4)
            print("SELFTEST OK", base, flush=True)
            try:
                window.destroy()
            except Exception:
                pass
        threading.Thread(target=closer, daemon=True).start()

    threading.Thread(target=polish_window, args=(TITLE,), daemon=True).start()
    # private_mode=False：pywebview 默认每次用临时隐私配置，localStorage 关窗即清，
    # 用户的设置/关注点/图层全都丢。关掉隐私模式后 WebView2 配置落在用户目录持久化；
    # 另有服务端 settings.json 兜底（前端关窗时 sendBeacon），双保险。
    try:
        webview.start(gui="edgechromium", debug=False, private_mode=False)
    except TypeError:      # 旧版 pywebview 没有该参数
        webview.start(gui="edgechromium", debug=False)
    try:
        srv.shutdown()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
