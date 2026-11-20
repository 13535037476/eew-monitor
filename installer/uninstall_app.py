"""震线 EEW 卸载程序

由安装包写到安装目录里，注册表 UninstallString 指向它：

    "C:\\...\\震线EEW\\uninstall.exe"

做的事：
  1. 读同目录的 install.json（安装时写的），或退回注册表
  2. 确认框里列清楚要删什么，可选「连设置和录像一起删」
  3. 结束正在运行的 震线EEW.exe
  4. 删桌面 / 开始菜单快捷方式、注册表卸载项
  5. 删安装目录（自己删不掉自己，最后交给一个分离的 cmd 收尾）

参数：--silent 不弹窗（更新前的静默替换用）
"""
import json
import os
import shutil
import subprocess
import sys
import time
import winreg

APP_NAME = "震线 EEW"
APP_ID = "EEWMonitor"
APP_EXE = "震线EEW.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall" + "\\" + APP_ID


def app_dir():
    return os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))


def read_install():
    p = os.path.join(app_dir(), "install.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return {
                "dir": winreg.QueryValueEx(k, "InstallLocation")[0],
                "version": winreg.QueryValueEx(k, "DisplayVersion")[0],
            }
    except Exception:
        return {}


def data_dir_of(d):
    return os.path.join(d, "data")


def size_of(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for n in files:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024.0


def shortcut_paths():
    home = os.path.expanduser("~")
    desk = os.path.join(home, "Desktop")
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOPDIRECTORY = 0x0010，兼容被 OneDrive 搬走的桌面
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
        if buf.value:
            desk = buf.value
    except Exception:
        pass
    appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    start = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")
    return [os.path.join(desk, APP_NAME + ".lnk"),
            os.path.join(start, APP_NAME + ".lnk"),
            os.path.join(start, APP_NAME, APP_NAME + ".lnk"),
            os.path.join(start, APP_NAME, "卸载 " + APP_NAME + ".lnk")]


def kill_app():
    try:
        subprocess.run(["taskkill", "/F", "/IM", APP_EXE],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=0x08000000, timeout=10)
    except Exception:
        pass


def remove_shortcuts():
    for p in shortcut_paths():
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass
    # 开始菜单里那个空文件夹
    try:
        folder = os.path.join(os.path.dirname(shortcut_paths()[2]))
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    except Exception:
        pass


def remove_registry():
    for root, key in ((winreg.HKEY_CURRENT_USER, RUN_KEY),
                      (winreg.HKEY_CURRENT_USER, "Software\\" + APP_ID)):
        try:
            winreg.DeleteKey(root, key)
        except Exception:
            pass


def schedule_self_delete(d, keep_data):
    """自己还在这个目录里跑，删不掉自己：交给一个分离的 cmd，等进程退出再删。"""
    # 保留设置时不能 rd /s /q 整个目录，否则 data\ 也一起没了——
    # 这里改成「删掉 data 以外的全部」，data\ 原地留着，路径在界面上写清楚了。
    bat = os.path.join(os.environ.get("TEMP", d), "eew_uninst_%d.cmd" % int(time.time()))
    body = "@echo off\r\n"
    # 多等两秒：uninstall.exe 自己还没退干净时 del 会失败
    body += "ping -n 5 127.0.0.1 >nul\r\n"
    # 注意：这一段绝不能用 % 格式化——bat 里 for 变量必须写成 %%F，
    # 上次用 %d 填路径，Python 把 %% 吃成了 %，cmd 收到 %F 直接不执行。
    if keep_data:
        # 只清文件与子目录，名字是 data 的放过
        body += ('for %%F in ("' + d + '\\*") do if /I not "%%~nxF"=="data" '
                 'del /f /q "%%~fF" >nul 2>nul\r\n')
        body += ('for /d %%D in ("' + d + '\\*") do if /I not "%%~nxD"=="data" '
                 'rd /s /q "%%~fD" >nul 2>nul\r\n')
    else:
        body += 'rd /s /q "' + d + '"\r\n'
    body += 'del /f /q "%~f0"\r\n'
    try:
        with open(bat, "w", encoding="mbcs", errors="ignore") as f:
            f.write(body)
        subprocess.Popen(["cmd", "/c", bat],
                         creationflags=0x08000000,          # CREATE_NO_WINDOW，照样活过父进程退出
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def do_uninstall(d, keep_data, log=None):
    log = log or (lambda s: None)
    log("结束正在运行的程序…")
    kill_app()
    time.sleep(0.4)
    log("删除快捷方式…")
    remove_shortcuts()
    log("清理注册表…")
    remove_registry()
    if not keep_data:
        log("删除设置、账号与录像…")
    log("删除程序文件…")
    # 先尽量删（除了自己）
    me = os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)
    for root, dirs, files in os.walk(d, topdown=False):
        for n in files:
            p = os.path.join(root, n)
            if os.path.abspath(p) == me:
                continue
            if keep_data and os.path.abspath(p).startswith(data_dir_of(d) + os.sep):
                continue
            try:
                os.remove(p)
            except Exception:
                pass
        for n in dirs:
            p = os.path.join(root, n)
            if keep_data and os.path.abspath(p) == data_dir_of(d):
                continue
            try:
                os.rmdir(p)
            except Exception:
                pass
    schedule_self_delete(d, keep_data)


# ---------------------------------------------------------------- 界面
def run_ui():
    import tkinter as tk
    from tkinter import messagebox

    info = read_install()
    d = info.get("dir") or app_dir()
    bg, panel, fg, dim, accent = "#14171c", "#101318", "#eef2f6", "#8b95a5", "#f0a020"

    root = tk.Tk()
    root.title("卸载 " + APP_NAME)
    root.configure(bg=bg)
    root.resizable(False, False)
    w, h = 560, 360
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
    try:
        ico = os.path.join(app_dir(), "icon.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:
        pass

    tk.Label(root, text="卸载 " + APP_NAME, bg=bg, fg=fg,
             font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w", padx=26, pady=(24, 4))
    tk.Label(root, text="安装位置：" + d, bg=bg, fg=dim,
             font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=26)

    box = tk.Frame(root, bg=panel, highlightthickness=1, highlightbackground="#262b33")
    box.pack(fill="both", expand=True, padx=22, pady=14)
    try:
        sz = human(size_of(d))
    except Exception:
        sz = "—"
    tk.Label(box, text="将会删除：程序文件（%s）、桌面与开始菜单快捷方式、注册表卸载项。" % sz,
             bg=panel, fg=fg, wraplength=470, justify="left",
             font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(12, 4))
    keep = tk.BooleanVar(value=True)
    cb = tk.Checkbutton(box, text="保留设置、账号与录像（%s）" % data_dir_of(d),
                        variable=keep, bg=panel, fg=dim, selectcolor=panel,
                        activebackground=panel, activeforeground=fg,
                        font=("Microsoft YaHei UI", 9), anchor="w", justify="left")
    cb.pack(anchor="w", padx=10, pady=(2, 10))

    status = tk.Label(root, text="", bg=bg, fg=dim, font=("Microsoft YaHei UI", 9))
    status.pack(anchor="w", padx=26)

    row = tk.Frame(root, bg=bg)
    row.pack(fill="x", padx=22, pady=(4, 18))

    def flat(parent, text, cmd, bgc, fgc):
        b = tk.Button(parent, text=text, command=cmd, bg=bgc, fg=fgc, bd=0,
                      relief="flat", padx=18, pady=7, cursor="hand2",
                      activebackground=bgc, activeforeground=fgc,
                      font=("Microsoft YaHei UI", 9))
        return b

    def go():
        if not messagebox.askyesno("确认卸载", "确定要卸载 %s 吗？" % APP_NAME):
            return
        btn_ok.config(state="disabled", bg="#3a3f47")
        btn_no.config(state="disabled")
        status.config(text="正在卸载…")

        def work():
            do_uninstall(d, keep.get(), lambda s: status.config(text=s))
            status.config(text="已卸载，窗口即将关闭。")
            time.sleep(0.8)
            root.after(0, root.destroy)

        import threading
        threading.Thread(target=work, daemon=True).start()

    btn_no = flat(row, "取消", root.destroy, "#22262d", "#c9d2dd")
    btn_no.pack(side="right", padx=(8, 0))
    btn_ok = flat(row, "卸载", go, "#8a2b20", "#ffe9e4")
    btn_ok.pack(side="right")

    root.mainloop()


def main():
    silent = "--silent" in sys.argv
    info = read_install()
    d = info.get("dir") or app_dir()
    if silent:
        do_uninstall(d, True)
        return 0
    run_ui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
