# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件 exe，内嵌 WebView2 窗口，不依赖外部浏览器

    pyinstaller --noconfirm eew.spec

产物：dist/震线EEW.exe
"""
from PyInstaller.utils.hooks import collect_all

datas = [("web", "web"), ("data/jp_places.json", "data"), ("data/coast_places.json", "data"), ("data/knet_stations.json", "data"), ("data/seafloor_stations.json", "data"), ("data/changelog.json", "data"), ("data/palert_stations.json", "data"), ("data/tokens.json", "data"), ("data/fe_regions.json", "data"), ("data/tt_ak135.json", "data"), ("data/tt_jma2001.json", "data")]
binaries = []
hiddenimports = []

for pkg in ("webview", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += ["bili", "cover", "qrcode", "qrcode.image.svg",
                  "webview.platforms.edgechromium", "clr", "pythonnet",
                  "PIL.Image", "PIL.JpegImagePlugin", "PIL.ImageFile"]

block_cipher = None

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="震线EEW",
    icon="web/icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
