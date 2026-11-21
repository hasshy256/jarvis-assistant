@echo off
rem Jarvis setup for a new PC. Double-click this file. Options are passed through to setup.ps1
rem   (e.g. setup.bat -SkipOllama -WithVoicevox -NoAutostart)
chcp 65001 > nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
