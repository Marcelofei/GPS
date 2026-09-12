' MesclarTreino.vbs
'
' Duplo-clique nisso pra mesclar a FC do relógio com os dados do
' dashboard, sem digitar nada no terminal.
'
' COMO USAR:
'   1. Na primeira vez, dê duplo-clique — ele cria uma pasta "mesclagem"
'      do lado deste arquivo e te avisa.
'   2. Depois de cada treino, coloque DENTRO da pasta "mesclagem":
'        - o .fit da atividade, exportado do Garmin Connect
'        - o treino_samples.json, baixado do dashboard
'      (apague os da vez anterior antes, senão ele não sabe qual é qual)
'   3. Dê duplo-clique de novo neste .vbs. Ele mescla sozinho e mostra
'      uma caixinha com o resultado (quantas amostras conseguiram FC).
'   4. O arquivo final (.tcx) aparece na mesma pasta "mesclagem", pronto
'      pra subir no Garmin Connect ou TrainingPeaks.

Dim fso, objShell, scriptDir, mesclagemDir
Set fso = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
mesclagemDir = scriptDir & "\mesclagem"

' ---- acha o python.exe direto pelo caminho real de instalação, mesma ----
' ---- lição aprendida com o IniciarDashboard.vbs (bare "python" via PATH ----
' ---- nem sempre resolve, dependendo do contexto de execução) ----
Function FindPythonExe()
    Dim baseDir, folder, subfolder, candidate
    FindPythonExe = ""

    baseDir = objShell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python")
    If fso.FolderExists(baseDir) Then
        Set folder = fso.GetFolder(baseDir)
        For Each subfolder In folder.SubFolders
            candidate = subfolder.Path & "\python.exe"
            If fso.FileExists(candidate) Then
                FindPythonExe = candidate
                Exit Function
            End If
        Next
    End If

    baseDir = objShell.ExpandEnvironmentStrings("%ProgramFiles%")
    If fso.FolderExists(baseDir) Then
        Set folder = fso.GetFolder(baseDir)
        For Each subfolder In folder.SubFolders
            If InStr(1, subfolder.Name, "Python3", vbTextCompare) = 1 Then
                candidate = subfolder.Path & "\python.exe"
                If fso.FileExists(candidate) Then
                    FindPythonExe = candidate
                    Exit Function
                End If
            End If
        Next
    End If
End Function

Dim pythonExePath
pythonExePath = FindPythonExe()
If pythonExePath = "" Then
    pythonExePath = "python.exe"  ' fallback: tenta pelo PATH mesmo assim
End If

' ---- cria a pasta na primeira vez ----
If Not fso.FolderExists(mesclagemDir) Then
    fso.CreateFolder(mesclagemDir)
    MsgBox "Criei a pasta 'mesclagem' do lado deste arquivo." & vbCrLf & vbCrLf & _
           "Depois de cada treino, coloque ali dentro:" & vbCrLf & _
           "  1) o .fit da atividade (exportado do Garmin Connect)" & vbCrLf & _
           "  2) o treino_samples.json (baixado do dashboard)" & vbCrLf & vbCrLf & _
           "Depois rode este atalho de novo.", vbInformation, "Mesclar treino"
    WScript.Quit
End If

' ---- procura exatamente 1 .fit e 1 .json na pasta ----
Dim folder, file, fitPath, jsonPath, fitCount, jsonCount
fitCount = 0
jsonCount = 0
Set folder = fso.GetFolder(mesclagemDir)
For Each file In folder.Files
    Select Case LCase(fso.GetExtensionName(file.Name))
        Case "fit"
            fitPath = file.Path
            fitCount = fitCount + 1
        Case "json"
            jsonPath = file.Path
            jsonCount = jsonCount + 1
    End Select
Next

If fitCount <> 1 Or jsonCount <> 1 Then
    MsgBox "Esperava exatamente 1 arquivo .fit e 1 arquivo .json dentro de:" & vbCrLf & _
           mesclagemDir & vbCrLf & vbCrLf & _
           "Encontrei " & fitCount & " arquivo(s) .fit e " & jsonCount & " arquivo(s) .json." & vbCrLf & vbCrLf & _
           "Apague os arquivos antigos da pasta antes de colocar os novos.", _
           vbExclamation, "Mesclar treino"
    WScript.Quit
End If

' ---- monta o nome de saída com data/hora, pra nunca sobrescrever sem querer ----
Dim outName, outPath, q, cmd
outName = "treino_mesclado_" & Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2) & _
          "_" & Right("0" & Hour(Now), 2) & Right("0" & Minute(Now), 2) & Right("0" & Second(Now), 2) & ".tcx"
outPath = mesclagemDir & "\" & outName

q = Chr(34)
cmd = q & pythonExePath & q & " " & q & scriptDir & "\merge_watch_hr.py" & q & " " & _
      q & fitPath & q & " " & q & jsonPath & q & " " & q & outPath & q

' ---- roda e CAPTURA a saída (Exec, não Run, pra conseguir ler o resultado) ----
Dim exec, stdoutText, stderrText
Set exec = objShell.Exec(cmd)
Do While exec.Status = 0
    WScript.Sleep 100
Loop

stdoutText = exec.StdOut.ReadAll()
stderrText = exec.StdErr.ReadAll()

If exec.ExitCode <> 0 Then
    MsgBox "Erro ao mesclar:" & vbCrLf & vbCrLf & stdoutText & stderrText, vbCritical, "Mesclar treino"
Else
    MsgBox "Mesclagem concluída!" & vbCrLf & vbCrLf & stdoutText & vbCrLf & _
           "Arquivo pronto em:" & vbCrLf & outPath, vbInformation, "Mesclar treino"
    objShell.Run q & mesclagemDir & q  ' abre a pasta pra você já ver o arquivo pronto
End If
