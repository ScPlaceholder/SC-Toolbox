' SC_Toolbox — Installed version launcher
' Uses the bundled Python in the python\ subdirectory.
' No system Python required.

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = appDir

pythonw = appDir & "\python\pythonw.exe"
pythonExe = appDir & "\python\python.exe"

If fso.FileExists(pythonw) Then
    WshShell.Run """" & pythonw & """ """ & appDir & "\skill_launcher.py"" 100 100 500 550 0.95 nul", 0, False
ElseIf fso.FileExists(pythonExe) Then
    WshShell.Run """" & pythonExe & """ """ & appDir & "\skill_launcher.py"" 100 100 500 550 0.95 nul", 0, False
Else
    MsgBox "Bundled Python not found. Please reinstall SC Toolbox.", vbCritical, "SC Toolbox"
End If
