' Jarvis remote server launcher (no console window). Registered for auto-start in the Startup folder.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
py = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\jarvis\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then WScript.Quit 1
sh.CurrentDirectory = dir
sh.Run """" & py & """ """ & dir & "\remote.py""", 0, False
