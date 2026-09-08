' Akso Workbench 桌面版启动器（双击运行，无控制台窗口）
' 等价命令行：uv run --extra desktop pythonw shell\shell.py
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    MsgBox "未找到虚拟环境：" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "请先执行：uv sync --extra dev --extra desktop", 16, "Akso Workbench"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run """" & pythonw & """ """ & root & "\shell\shell.py""", 0, False
