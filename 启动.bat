@echo off
chcp 65001 >nul
title 简历智能筛选与评估系统
echo 正在启动系统...
echo 浏览器打开: http://127.0.0.1:8000
echo 按 Ctrl+C 停止
echo.

cd /d "%~dp0"

rem 优先使用项目内的虚拟环境，否则使用系统 Python
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" main.py
) else if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" main.py
) else (
    python main.py
)
pause
