' start.vbs -- launches the Transcription GUI without a console window.
'
' Pointed to by the Desktop and Start Menu shortcuts.
' The GUI itself opens its own native window via NiceGUI + WebView2.

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
repoDir   = fso.GetParentFolderName(scriptDir)

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = repoDir
' Run hidden (0), don't wait (False)
sh.Run "uv run --extra transcribe transcription gui", 0, False
