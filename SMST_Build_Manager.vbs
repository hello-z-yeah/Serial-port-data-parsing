Option Explicit
Dim shell, fso, root, gui, candidates, i, pythonw, pythonexe, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
gui = fso.BuildPath(root, "SMST_Build_Manager.pyw")

candidates = Array( _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%ProgramFiles%\Python314\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%ProgramFiles%\Python313\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%ProgramFiles%\Python312\pythonw.exe"), _
  shell.ExpandEnvironmentStrings("%ProgramFiles%\Python311\pythonw.exe") _
)
pythonw = ""
For i = 0 To UBound(candidates)
  If fso.FileExists(candidates(i)) Then
    pythonw = candidates(i)
    Exit For
  End If
Next

If pythonw = "" Then
  pythonexe = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python314\python.exe")
  If fso.FileExists(pythonexe) Then pythonw = pythonexe
End If

If pythonw = "" Then
  MsgBox "未找到 Python 3.11-3.14。请右键 SMST_Build_Manager.pyw，选择使用 Python 打开。", vbCritical, "Super Max Serial Tool"
  WScript.Quit 1
End If

command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & gui & Chr(34)
shell.Run command, 1, False
