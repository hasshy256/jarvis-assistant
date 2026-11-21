' Jarvis launcher (no console window). Works on any PC: uses %LOCALAPPDATA% and this file's own folder.
' Double-click to start. setup.ps1 also registers this file for auto-start.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
py = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\jarvis\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
  MsgBox "Jarvis is not set up on this PC yet. Run setup.bat in this folder first.", 48, "J.A.R.V.I.S."
  WScript.Quit 1
End If
sh.CurrentDirectory = dir
sh.Run """" & py & """ """ & dir & "\jarvis.py""", 0, False
