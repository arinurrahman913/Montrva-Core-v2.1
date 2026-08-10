' Menjalankan backend Montrva tanpa jendela (pythonw), untuk auto-start
' saat login Windows. Shortcut ke file ini ditaruh di Startup folder.
' Portable: menemukan root repo dari lokasi script ini (scripts/..).
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)   ' ...\scripts
repoDir   = fso.GetParentFolderName(scriptDir)                ' ...\ (root repo)

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = repoDir
' 0 = jendela disembunyikan, False = jangan tunggu (jalan di background)
sh.Run "pythonw backend\app.py", 0, False
