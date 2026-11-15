@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 不用 WebView2：拿系统里的 Edge / Chrome 的 --app 模式开一个无地址栏窗口。
rem 找不到浏览器就退回默认浏览器标签页。关掉这个黑窗口 = 退出服务。
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% server.py --port 8765 --app
if errorlevel 1 pause
