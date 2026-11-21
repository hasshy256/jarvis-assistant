@echo off
chcp 65001 > nul
set PYGAME_HIDE_SUPPORT_PROMPT=1
set PYTHONIOENCODING=utf-8
rem 合言葉（JARVIS_TOKEN）は Windows の環境変数から読む。待ち受けを変えるなら JARVIS_REMOTE_HOST / JARVIS_REMOTE_PORT
"%LOCALAPPDATA%\jarvis\.venv\Scripts\python.exe" "%~dp0remote.py"
pause
