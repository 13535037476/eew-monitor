"""打安装包：应用 exe + 卸载程序 + 品牌安装向导 → 一个 Setup exe

    python installer/build_installer.py            # 用 dist 里现成的应用 exe
    python installer/build_installer.py --rebuild   # 先重打应用 exe（eew.spec），再打安装包

产物默认写到项目外面（E:\\eew_release），不会碰项目目录里的任何文件：
    E:\\eew_release\\震线EEW-Setup-<版本>.exe
    E:\\eew_release\\update.json
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAYLOAD = os.path.join(HERE, "payload")
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
OUT = os.environ.get("EEW_RELEASE_DIR") or r"E:\eew_release"
ICON = os.path.join(ROOT, "web", "icon.ico")
# 主程序用什么解释器打（它有 pywebview 等依赖），安装器/卸载器用当前解释器——
# 那两个是 tkinter 界面，必须用带 tcl/tk 的 Python，主环境里恰恰没有。
APP_PY = os.environ.get("EEW_APP_PY") or sys.executable


def sh(cmd, **kw):
    print("  $", " ".join(cmd) if isinstance(cmd, list) else cmd)
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise SystemExit("命令失败（%d）：%s" % (r.returncode, cmd))
    return r


def version():
    with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r'^VERSION\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    raise SystemExit("server.py 里找不到 VERSION")


def notes():
    try:
        with open(os.path.join(ROOT, "data", "changelog.json"), encoding="utf-8") as f:
            d = json.load(f)
        e = (d.get("entries") or [{}])[0]
        return "\n".join("· " + x for x in e.get("items", [])[:8])
    except Exception:
        return ""


def pyinstaller(args, py=None):
    sh([py or sys.executable, "-m", "PyInstaller"] + args, cwd=ROOT)


def main():
    rebuild = "--rebuild" in sys.argv
    ver = version()
    print("版本:", ver)

    if rebuild:
        print("[1/4] 重打应用 exe（eew.spec）…")
        pyinstaller(["--noconfirm", "--clean", "eew.spec"], py=APP_PY)
    else:
        print("[1/4] 跳过应用 exe（用 dist 里现成的）")

    app_exe = os.path.join(ROOT, "dist", "震线EEW.exe")
    if not os.path.isfile(app_exe):
        raise SystemExit("找不到 dist\\震线EEW.exe，先跑 pyinstaller --noconfirm eew.spec")

    print("[2/4] 打卸载程序…")
    os.makedirs(DIST, exist_ok=True)
    pyinstaller(["--noconfirm", "--clean", "--onefile", "--windowed",
                 "--icon", ICON, "--name", "uninstall",
                 "--distpath", DIST, "--workpath", os.path.join(BUILD, "uninst"),
                 "--specpath", BUILD,
                 os.path.join(HERE, "uninstall_app.py")])
    uninst = os.path.join(DIST, "uninstall.exe")
    if not os.path.isfile(uninst):
        raise SystemExit("卸载程序没打出来")

    print("[3/4] 组装 payload…")
    if os.path.isdir(PAYLOAD):
        shutil.rmtree(PAYLOAD, ignore_errors=True)
    os.makedirs(PAYLOAD, exist_ok=True)
    shutil.copy2(app_exe, os.path.join(PAYLOAD, "震线EEW.exe"))
    shutil.copy2(uninst, os.path.join(PAYLOAD, "uninstall.exe"))
    shutil.copy2(os.path.join(ROOT, "web", "icon.ico"), os.path.join(PAYLOAD, "icon.ico"))

    update_url = ("https://raw.githubusercontent.com/13535037476/eew-monitor/main/update.json")
    meta = {"version": ver, "notes": notes(), "update_url": update_url,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(PAYLOAD, "version.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("[4/4] 打安装向导…")
    name = "震线EEW-Setup-%s" % ver
    pyinstaller(["--noconfirm", "--clean", "--onefile", "--windowed",
                 "--icon", ICON, "--name", name,
                 "--add-data", "%s;payload" % PAYLOAD,
                 "--distpath", DIST, "--workpath", os.path.join(BUILD, "setup"),
                 "--specpath", BUILD,
                 os.path.join(HERE, "setup_app.py")])
    setup = os.path.join(DIST, name + ".exe")
    if not os.path.isfile(setup):
        raise SystemExit("安装包没打出来")

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, name + ".exe")
    shutil.copy2(setup, dst)

    upd = {
        "version": ver,
        "url": "https://github.com/13535037476/eew-monitor/releases/latest/download/" + name + ".exe",
        "size": os.path.getsize(dst),
        "notes": meta["notes"],
        "published": time.strftime("%Y-%m-%d"),
    }
    for p in (os.path.join(OUT, "update.json"), os.path.join(ROOT, "update.json")):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(upd, f, ensure_ascii=False, indent=2)

    print()
    print("完成：", dst, "(%.1f MB)" % (os.path.getsize(dst) / 1048576.0))
    print("清单：", os.path.join(OUT, "update.json"))
    print("注意：安装包只写到这里，安装时默认装到 %LOCALAPPDATA%\\Programs\\震线 EEW")


if __name__ == "__main__":
    main()
