@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 优先用项目自带虚拟环境，没有就用系统 Python
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
) else (
    start "" pythonw "main.py"
)
