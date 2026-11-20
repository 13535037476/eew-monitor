"""震线 EEW 安装程序

一个自带品牌的安装向导：自定义安装路径、桌面/开始菜单快捷方式、
注册表卸载项（「应用和功能」里能看到）、可选安装后立即启动。

命令行（给自动更新用）：
    震线EEW-Setup.exe /S            静默安装 / 覆盖升级到上次的目录
    震线EEW-Setup.exe /S /D=D:\\Apps\\EEW
    震线EEW-Setup.exe /S --relaunch 装完把程序拉起来（更新流程用）

打包：
    pyinstaller --onefile --windowed --icon web/icon.ico ^
        --add-data "payload;payload" --name "震线EEW-Setup" installer/setup_app.py
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg

APP_NAME = "震线 EEW"
APP_ID = "EEWMonitor"
APP_EXE = "震线EEW.exe"
UNINST_EXE = "uninstall.exe"
PUBLISHER = "震线 EEW 项目"
HOMEPAGE = "https://github.com/13535037476/eew-monitor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall" + "\\" + APP_ID
CFG_KEY = "Software\\" + APP_ID


# ------------------------------------------------------------------ 工具
def payload_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, "payload"), os.path.join(base, "installer", "payload")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(base, "payload")


def read_payload_meta():
    try:
        with open(os.path.join(payload_dir(), "version.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": "1.0.0", "notes": "", "update_url": ""}


def default_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "Programs", APP_NAME)


def existing_install():
    """上次装到哪了：先看注册表，再看默认目录。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            d = winreg.QueryValueEx(k, "InstallLocation")[0]
            if d:
                return d
    except Exception:
        pass
    d = default_dir()
    if os.path.isfile(os.path.join(d, "install.json")):
        return d
    return ""


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".eew_probe")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True, ""
    except Exception as e:
        return False, str(e)


def ps_shortcut(lnk, target, workdir, icon, desc):
    """用 PowerShell 的 WScript.Shell 建 .lnk；用 EncodedCommand 避开中文/引号问题。"""
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.IconLocation='%s';"
        "$s.Description='%s';$s.Save()"
    ) % (lnk.replace("'", "''"), target.replace("'", "''"), workdir.replace("'", "''"),
         icon.replace("'", "''"), desc.replace("'", "''"))
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                    "-EncodedCommand", enc],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=0x08000000, timeout=30)


def desktop_dir():
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
        if buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def start_menu_dir():
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def dir_size(path):
    total = 0
    for root, _d, files in os.walk(path):
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


def looks_like_source(d):
    """这个目录是不是一份源码工程。

    安装 = 往目标目录里覆盖一堆同名文件，卸载 = 把整个目录删掉。
    如果目标正好是开发目录（有 server.py + web/ + eew.spec），
    装一次就等于往工程里灌一份打包产物，卸载一次源码全没。
    这种情况直接拒绝，绝不留「可以继续」的口子。
    """
    try:
        return (os.path.isfile(os.path.join(d, "server.py"))
                and os.path.isdir(os.path.join(d, "web"))
                and os.path.isfile(os.path.join(d, "eew.spec")))
    except Exception:
        return False


def install_to(d, meta, desk_lnk=True, menu_lnk=True, log=print):
    """真正的安装：复制 → 写 install.json → 快捷方式 → 注册表。"""
    d = os.path.normpath(d)          # /D= 传进来的路径可能带着正斜杠
    src = payload_dir()
    if looks_like_source(d):
        raise RuntimeError(
            "这个目录是程序源码工程，不能装在这里。\n"
            "装进去会覆盖源码，卸载时还会把整个工程删掉。\n"
            "请换一个空目录（默认的 %s 就很好）。" % default_dir())
    ok, err = writable_dir(d)
    if not ok:
        raise RuntimeError("这个目录不可写：%s\n%s\n换一个目录，或用管理员权限运行安装程序。" % (d, err))

    log("正在复制程序文件…")
    for name in os.listdir(src):
        s = os.path.join(src, name)
        t = os.path.join(d, name)
        if os.path.isdir(s):
            shutil.copytree(s, t, dirs_exist_ok=True)
        else:
            shutil.copy2(s, t)

    app = os.path.join(d, APP_EXE)
    if not os.path.isfile(app):
        raise RuntimeError("安装包不完整：找不到 " + APP_EXE)

    log("正在写入安装信息…")
    info = {
        "app": APP_NAME,
        "version": meta.get("version", "0"),
        "dir": d,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "update_url": meta.get("update_url", ""),
        "homepage": HOMEPAGE,
    }
    with open(os.path.join(d, "install.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    icon = os.path.join(d, "icon.ico")
    if not os.path.exists(icon):
        icon = app

    if desk_lnk:
        log("创建桌面快捷方式…")
        try:
            ps_shortcut(os.path.join(desktop_dir(), APP_NAME + ".lnk"),
                        app, d, icon + ",0", APP_NAME + " · 中日地震预警")
        except Exception:
            pass

    if menu_lnk:
        log("创建开始菜单项…")
        folder = os.path.join(start_menu_dir(), APP_NAME)
        try:
            os.makedirs(folder, exist_ok=True)
            ps_shortcut(os.path.join(folder, APP_NAME + ".lnk"),
                        app, d, icon + ",0", APP_NAME + " · 中日地震预警")
            ps_shortcut(os.path.join(folder, "卸载 " + APP_NAME + ".lnk"),
                        os.path.join(d, UNINST_EXE), d, icon + ",0", "卸载 " + APP_NAME)
        except Exception:
            pass

    log("登记到「应用和功能」…")
    size_kb = int(dir_size(d) / 1024)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, meta.get("version", "0"))
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, d)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, icon + ",0")
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, '"%s"' % os.path.join(d, UNINST_EXE))
        winreg.SetValueEx(k, "QuietUninstallString", 0, winreg.REG_SZ,
                          '"%s" --silent' % os.path.join(d, UNINST_EXE))
        winreg.SetValueEx(k, "URLInfoAbout", 0, winreg.REG_SZ, HOMEPAGE)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CFG_KEY) as k:
        winreg.SetValueEx(k, "InstallDir", 0, winreg.REG_SZ, d)
        winreg.SetValueEx(k, "Version", 0, winreg.REG_SZ, meta.get("version", "0"))
        winreg.SetValueEx(k, "UpdateUrl", 0, winreg.REG_SZ, meta.get("update_url", ""))
    log("完成。")
    return app


def parse_silent_args(argv):
    d = ""
    relaunch = "--relaunch" in argv
    for a in argv:
        if a.upper().startswith("/D="):
            d = a[3:].strip('"')
        elif a.upper().startswith("--DIR="):
            d = a[6:].strip('"')
    return d, relaunch


# ------------------------------------------------------------------ 界面
def run_ui():
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from tkinter import ttk

    meta = read_payload_meta()
    ver = meta.get("version", "0")

    bg = "#14171c"
    panel = "#101318"
    card = "#1a1e25"
    line = "#2a2f38"
    fg = "#eef2f6"
    dim = "#8b95a5"
    accent = "#f0a020"

    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.title("安装 " + APP_NAME + " " + ver)
    root.configure(bg=bg)
    root.resizable(False, False)
    W, H = 800, 500
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d+%d+%d" % (W, H, (sw - W) // 2, (sh - H) // 2))
    try:
        ic = os.path.join(payload_dir(), "icon.ico")
        if os.path.exists(ic):
            root.iconbitmap(ic)
    except Exception:
        pass

    # ---------------- 左侧品牌栏
    left = tk.Frame(root, bg=panel, width=232)
    left.pack(side="left", fill="y")
    left.pack_propagate(False)
    cv = tk.Canvas(left, bg=panel, highlightthickness=0, width=232, height=110)
    cv.pack(pady=(46, 6))
    # 一条地震波形：几何折线，不依赖图片
    pts = []
    import math
    for i in range(0, 232, 3):
        x = i
        t = i / 232.0
        amp = 16 * math.exp(-((t - 0.52) ** 2) / 0.012)
        y = 56 + amp * math.sin(i * 0.55) * (0.6 + 0.4 * math.sin(i * 0.13))
        pts += [x, y]
    cv.create_line(*pts, fill=accent, width=2, smooth=False)
    tk.Label(left, text=APP_NAME, bg=panel, fg=fg,
             font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w", padx=24)
    tk.Label(left, text="中日地震预警监视台", bg=panel, fg=dim,
             font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=24)
    tk.Label(left, text="v" + ver, bg=panel, fg=accent,
             font=("Consolas", 10)).pack(anchor="w", padx=24, pady=(10, 0))
    tk.Label(left, text="开源 · 非官方 · 非护命用途\n气象厅 / 中国地震台网 公开数据",
             bg=panel, fg="#6b7688", justify="left",
             font=("Microsoft YaHei UI", 8)).pack(side="bottom", anchor="w", padx=24, pady=20)

    # ---------------- 右侧内容
    right = tk.Frame(root, bg=bg)
    right.pack(side="left", fill="both", expand=True)

    page1 = tk.Frame(right, bg=bg)
    page2 = tk.Frame(right, bg=bg)

    tk.Label(page1, text="选择安装位置", bg=bg, fg=fg,
             font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", padx=30, pady=(34, 2))
    tk.Label(page1, text="程序会装进这个文件夹；设置、账号和录像也放在它的 data\\ 里。",
             bg=bg, fg=dim, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=30)

    rowp = tk.Frame(page1, bg=bg)
    rowp.pack(fill="x", padx=30, pady=(16, 0))
    prev = existing_install()
    path_var = tk.StringVar(value=prev or default_dir())
    entry = tk.Entry(rowp, textvariable=path_var, bg=card, fg=fg, bd=0, relief="flat",
                     insertbackground=accent, font=("Consolas", 10),
                     highlightthickness=1, highlightbackground=line, highlightcolor=accent)
    entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))

    def browse():
        d = filedialog.askdirectory(title="选择安装位置", initialdir=path_var.get() or "C:\\")
        if d:
            path_var.set(os.path.normpath(d))

    def flat_btn(parent, text, cmd, bgc, fgc, active=None, pad=(16, 6)):
        return tk.Button(parent, text=text, command=cmd, bg=bgc, fg=fgc, bd=0, relief="flat",
                         padx=pad[0], pady=pad[1], cursor="hand2",
                         activebackground=active or bgc, activeforeground=fgc,
                         font=("Microsoft YaHei UI", 9))

    flat_btn(rowp, "浏览…", browse, "#252a32", "#d6dee8", "#2f353e").pack(side="left")

    hint = tk.Label(page1, text="", bg=bg, fg=dim, font=("Microsoft YaHei UI", 8))
    hint.pack(anchor="w", padx=30, pady=(6, 0))

    def refresh_hint(*_a):
        d = path_var.get().strip()
        try:
            if d and looks_like_source(d):
                hint.config(text="这是源码工程目录，不能装在这里：卸载会把整个工程删掉。换个目录。",
                            fg="#ff8a7a")
                return
            if d and os.path.isdir(d) and os.listdir(d):
                if os.path.isfile(os.path.join(d, "install.json")):
                    hint.config(text="这里已经装过一版，会就地覆盖升级（设置和录像保留）。", fg="#9ad0a8")
                else:
                    hint.config(text="这个文件夹不是空的：只覆盖同名文件，别的文件不动。", fg="#d8b26a")
            else:
                hint.config(text="", fg=dim)
        except Exception:
            pass

    path_var.trace_add("write", refresh_hint)
    refresh_hint()

    opt = tk.Frame(page1, bg=bg)
    opt.pack(anchor="w", padx=30, pady=(20, 0))
    desk_var = tk.BooleanVar(value=True)
    menu_var = tk.BooleanVar(value=True)
    run_var = tk.BooleanVar(value=True)
    for var, txt in ((desk_var, "创建桌面快捷方式"),
                     (menu_var, "在开始菜单里建一项（含「卸载」）"),
                     (run_var, "安装完成后立即启动")):
        tk.Checkbutton(opt, text=txt, variable=var, bg=bg, fg="#c9d2dd", selectcolor=card,
                       activebackground=bg, activeforeground=fg, bd=0, highlightthickness=0,
                       font=("Microsoft YaHei UI", 9), anchor="w").pack(anchor="w", pady=1)

    try:
        need = human(dir_size(payload_dir()))
    except Exception:
        need = "—"
    tk.Label(page1, text="需要约 %s 磁盘空间 · 安装到当前用户，不需要管理员权限" % need,
             bg=bg, fg="#6b7688", font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=30, pady=(18, 0))

    foot1 = tk.Frame(page1, bg=bg)
    foot1.pack(side="bottom", fill="x", padx=30, pady=22)
    status = tk.Label(foot1, text="", bg=bg, fg=dim, font=("Microsoft YaHei UI", 8))
    status.pack(side="left")

    # ---------------- 第二页：进度 / 完成
    tk.Label(page2, text="正在安装", bg=bg, fg=fg,
             font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", padx=30, pady=(34, 2))
    step = tk.Label(page2, text="准备中…", bg=bg, fg=dim, font=("Microsoft YaHei UI", 9))
    step.pack(anchor="w", padx=30)

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("eew.Horizontal.TProgressbar", troughcolor="#1c2027", bordercolor="#1c2027",
                    background=accent, lightcolor=accent, darkcolor=accent, thickness=8)
    bar = ttk.Progressbar(page2, style="eew.Horizontal.TProgressbar", mode="determinate", maximum=100)
    bar.pack(fill="x", padx=30, pady=(16, 0))

    done_box = tk.Frame(page2, bg=bg)
    done_title = tk.Label(done_box, text="安装完成", bg=bg, fg="#9ad0a8",
                          font=("Microsoft YaHei UI", 15, "bold"))
    done_title.pack(anchor="w")
    done_sub = tk.Label(done_box, text="", bg=bg, fg=dim, font=("Microsoft YaHei UI", 9))
    done_sub.pack(anchor="w", pady=(4, 0))
    done_btns = tk.Frame(done_box, bg=bg)
    done_btns.pack(anchor="w", pady=(14, 0))
    done_run = tk.BooleanVar(value=True)

    def open_dir(d):
        try:
            os.startfile(d)
        except Exception:
            pass

    def launch(app):
        try:
            subprocess.Popen([app], cwd=os.path.dirname(app), close_fds=True)
        except Exception:
            pass

    def finish():
        d = path_var.get().strip()
        app = os.path.join(d, APP_EXE)
        if done_run.get():
            launch(app)
        root.destroy()

    flat_btn(done_btns, "打开文件夹", lambda: open_dir(path_var.get().strip()), "#252a32", "#d6dee8",
             "#2f353e").pack(side="left")
    tk.Checkbutton(done_btns, text="立即启动", variable=done_run, bg=bg, fg="#c9d2dd",
                   selectcolor=card, activebackground=bg, activeforeground=fg, bd=0,
                   highlightthickness=0, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=12)

    foot2 = tk.Frame(page2, bg=bg)
    foot2.pack(side="bottom", fill="x", padx=30, pady=22)
    btn_finish = flat_btn(foot2, "完成", finish, accent, "#241a06", "#ffb733", (24, 8))
    btn_close = flat_btn(foot2, "关闭", root.destroy, "#22262d", "#c9d2dd", "#2c313a", (18, 8))

    def start_install():
        d = path_var.get().strip().rstrip("\\/")
        if not d:
            messagebox.showwarning("还没选位置", "请先选一个安装文件夹。")
            return
        if looks_like_source(d):
            messagebox.showerror("不能装在这里",
                                 "这个目录是源码工程，装进去会覆盖源码，\n"
                                 "卸载时还会把整个工程删掉。请换一个空目录。")
            return
        page1.pack_forget()
        page2.pack(fill="both", expand=True)
        bar["value"] = 0
        btn_finish.pack_forget()
        btn_close.pack_forget()

        def log(msg):
            root.after(0, lambda: (step.config(text=msg), bar.step(14)))

        def work():
            try:
                app = install_to(d, meta, desk_var.get(), menu_var.get(), log)
                root.after(0, lambda: finish_ok(app))
            except Exception as e:
                root.after(0, lambda: finish_err(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def finish_ok(app):
        bar["value"] = 100
        step.config(text="")
        done_sub.config(text="已安装到 " + path_var.get().strip() +
                            "\n桌面和开始菜单里都能找到「" + APP_NAME + "」。")
        done_box.pack(fill="both", expand=True, padx=30, pady=(18, 0))
        btn_finish.pack(side="right")

    def finish_err(msg):
        step.config(text="安装失败：" + msg, fg="#ff8a7a")
        btn_close.pack(side="right")

    flat_btn(foot1, "取消", root.destroy, "#22262d", "#c9d2dd", "#2c313a").pack(side="right")
    b = flat_btn(foot1, "开始安装", start_install, accent, "#241a06", "#ffb733", (24, 8))
    b.pack(side="right", padx=(0, 8))

    page1.pack(fill="both", expand=True)
    root.mainloop()


def main():
    argv = sys.argv[1:]
    silent = "/S" in argv or "--silent" in argv
    meta = read_payload_meta()
    if silent:
        d, relaunch = parse_silent_args(argv)
        d = d or existing_install() or default_dir()
        try:
            app = install_to(d, meta, True, True, lambda s: None)
            if relaunch:
                subprocess.Popen([app], cwd=d, close_fds=True)
            return 0
        except Exception as e:
            try:
                with open(os.path.join(os.environ.get("TEMP", "."), "eew_setup_error.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(str(e))
            except Exception:
                pass
            return 2
    run_ui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
