' PararDashboard.vbs
'
' Encerra o servidor iniciado pelo IniciarDashboard.vbs — necessário
' porque, rodando escondido (sem janela cmd), não tem como apertar
' Ctrl+C. Mata só o processo exato que foi iniciado (via PID salvo em
' dashboard.pid), não qualquer outro programa Python do seu PC.

Dim fso, objShell, scriptDir, pidFile, pid, f

Set fso = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pidFile = scriptDir & "\dashboard.pid"

If Not fso.FileExists(pidFile) Then
    MsgBox "Nenhum dashboard rodando no momento (não achei dashboard.pid).", vbInformation, "Dashboard do rolo"
    WScript.Quit
End If

Set f = fso.OpenTextFile(pidFile, 1)
pid = Trim(f.ReadLine)
f.Close

objShell.Run "taskkill /F /PID " & pid, 0, True
fso.DeleteFile(pidFile)

MsgBox "Dashboard encerrado.", vbInformation, "Dashboard do rolo"
