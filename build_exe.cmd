@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/2] 安装打包依赖（pywebview / pyinstaller）
python -m pip install --disable-pip-version-check -q -i https://mirrors.aliyun.com/pypi/simple pywebview pyinstaller
if errorlevel 1 goto fail

echo [2/2] 打包单文件 exe
python -m PyInstaller --noconfirm --clean eew.spec
if errorlevel 1 goto fail

echo.
echo 完成：dist\震线EEW.exe
echo 双击即可运行，窗口内嵌 WebView2，不会打开外部浏览器。
pause
exit /b 0

:fail
echo.
echo 打包失败，请检查上面的错误信息。
pause
exit /b 1
