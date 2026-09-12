' IniciarDashboard.vbs
'
' Duplo-clique nisso (ou num atalho na Área de Trabalho apontando pra
' isso) inicia o dashboard sem abrir nenhuma janela de terminal, e abre o
' navegador sozinho em alguns segundos.
'
' USO 1 — arquivo .fit padrão: só dê duplo-clique. Ele usa o arquivo
'         chamado "Treino.fit" na mesma pasta (troque o nome abaixo se
'         quiser outro padrão).
' USO 2 — escolher o .fit do dia: arraste o arquivo .fit desejado e
'         solte em cima deste .vbs (ou de um atalho dele) — ele usa o
'         arquivo que você arrastou em vez do padrão.
'
' Histórico de correções deste script (pra quem for mexer depois):
'   v1: usava WMI (Win32_Process.Create) — falhava com erro 9 "path not
'       found" porque o WMI roda num contexto que não enxerga o PATH
'       configurado só pro usuário (onde fica o pythonw.exe se você não
'       marcou "Install for all users" no instalador do Python).
'   v2: trocou pra WScript.Shell.Run confiando no PATH — mas sem
'       tratamento de erro, uma falha na hora de achar o pythonw.exe
'       ficava completamente silenciosa (nenhum processo, nenhum log,
'       nenhum aviso).
'   v3 (esta): busca o pythonw.exe diretamente pelo caminho real de
'       instalação (%LOCALAPPDATA%\Programs\Python\Python3*\), sem
'       depender do PATH de jeito nenhum, e qualquer falha no Run agora
'       aparece numa caixa de mensagem com o erro exato.

Dim fso, objShell, scriptDir, fitFile, q, cmd, pythonwPath

Set fso = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' ---- acha o pythonw.exe direto pelo caminho real, sem depender do PATH ----
Function FindPythonw()
    Dim baseDir, folder, subfolder, candidate
    FindPythonw = ""

    baseDir = objShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python")
    If fso.FolderExists(baseDir) Then
        Set folder = fso.GetFolder(baseDir)
        For Each subfolder In folder.SubFolders
            candidate = subfolder.Path & "\pythonw.exe"
            If fso.FileExists(candidate) Then
                FindPythonw = candidate
                Exit Function
            End If
        Next
    End If

    ' fallback: instalação "for all users" fica em Program Files, não em
    ' AppData\Local — confere esse caminho também antes de desistir
    baseDir = objShell.ExpandEnvironmentStrings("%ProgramFiles%")
    If fso.FolderExists(baseDir) Then
        Set folder = fso.GetFolder(baseDir)
        For Each subfolder In folder.SubFolders
            If InStr(1, subfolder.Name, "Python3", vbTextCompare) = 1 Then
                candidate = subfolder.Path & "\pythonw.exe"
                If fso.FileExists(candidate) Then
                    FindPythonw = candidate
                    Exit Function
                End If
            End If
        Next
    End If
End Function

pythonwPath = FindPythonw()

If pythonwPath = "" Then
    MsgBox "Não encontrei o pythonw.exe em nenhum dos caminhos de instalação" & vbCrLf & _
           "conhecidos (%LOCALAPPDATA%\Programs\Python\ ou %ProgramFiles%\)." & vbCrLf & vbCrLf & _
           "Abra o cmd e digite 'where pythonw' pra achar o caminho real, e me" & vbCrLf & _
           "avise qual apareceu — vou ajustar o script pra esse caminho.", vbCritical, "Dashboard do rolo"
    WScript.Quit
End If

' ---- decide qual .fit usar ----
If WScript.Arguments.Count > 0 Then
    fitFile = WScript.Arguments(0)   ' arquivo arrastado em cima do atalho
Else
    fitFile = scriptDir & "\Treino.fit"   ' <-- troque aqui se quiser outro nome padrão
End If

If Not fso.FileExists(fitFile) Then
    MsgBox "Não encontrei o arquivo de treino:" & vbCrLf & fitFile & vbCrLf & vbCrLf & _
           "Arraste o .fit do treino de hoje em cima deste atalho, ou coloque um arquivo" & vbCrLf & _
           "chamado 'Treino.fit' na pasta do projeto.", vbExclamation, "Dashboard do rolo"
    WScript.Quit
End If

' ---- monta e executa o comando, com tratamento de erro visível ----
q = Chr(34)
cmd = q & pythonwPath & q & " " & q & scriptDir & "\server.py" & q & " " & q & fitFile & q

objShell.CurrentDirectory = scriptDir

On Error Resume Next
Err.Clear
objShell.Run cmd, 0, False
If Err.Number <> 0 Then
    MsgBox "Erro ao iniciar o servidor (código " & Err.Number & "):" & vbCrLf & Err.Description & vbCrLf & vbCrLf & _
           "Comando que tentei rodar:" & vbCrLf & cmd, vbCritical, "Dashboard do rolo"
    On Error Goto 0
    WScript.Quit
End If
On Error Goto 0

' espera o Flask subir antes de abrir o navegador
WScript.Sleep 3000
objShell.Run "http://localhost:5000"
