@echo off
chcp 65001 > nul
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYGAME_HIDE_SUPPORT_PROMPT=1
"%LOCALAPPDATA%\jarvis\.venv\Scripts\python.exe" "%~dp0jarvis.py"
pause
