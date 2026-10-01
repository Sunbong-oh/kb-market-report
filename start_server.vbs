' Starts server.bat in the background with no console window (used at Windows logon).
' Kept ASCII-only; the folder is resolved from this script's own location.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c server.bat", 0, False
