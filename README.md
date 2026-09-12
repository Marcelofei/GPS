# Dashboard BLE para rolo indoor + exportação de treino

## Requisitos
- Bluetooth do PC (a maioria dos notebooks já tem BLE embutido; sem dongle)
- Rolo inteligente transmitindo por BLE (FTMS ou Cycling Power Service, com fallback para CSC)
- Relógio Garmin com "Broadcast Heart Rate" habilitado em modo BLE (confirme nas Configurações > Sensores e Acessórios do relógio — nem todo modelo suporta)

## Atenção real: conexão BLE exclusiva
BLE normalmente é 1-para-1. Se o relógio for gravar a atividade E simultaneamente transmitir FC por broadcast, confira se a conexão se mantém estável com os dois processos ao mesmo tempo — depende do firmware específico do seu relógio.

## Instalação
```bash
pip install -r requirements.txt --break-system-packages
```

## Uso

### Linux/Mac (terminal)
1. Monte o treino no Garmin Connect e baixe o `.fit` do workout planejado.
2. Ligue o rolo e ative o broadcast BLE de FC no relógio.
3. Rode:
```bash
python server.py caminho/do/treino.fit
```
4. Abra `http://localhost:5000` no navegador do monitor, F11 pra fullscreen.

### Windows (sem terminal)
Dê duplo-clique em `IniciarDashboard.vbs` (ou num atalho apontando pra ele na Área de Trabalho). Ele acha o `pythonw.exe` sozinho, sobe o servidor escondido e abre o navegador. Usa `Treino.fit` na mesma pasta por padrão, ou arraste outro `.fit` em cima do atalho. Para encerrar, dê duplo-clique em `PararDashboard.vbs`.

### Durante o treino
Pedale. FC/potência/cadência atualizam a cada 1s, etapas avançam sozinhas (ou pelo botão **Avançar etapa →**, que aparece quando a etapa atual é "open"). Use **Pausar/Retomar** pra congelar o cronômetro sem perder o progresso, e **Encerrar treino** pra finalizar a qualquer momento (o servidor continua rodando — dá pra exportar depois). A sidebar mostra as próximas etapas com duração e faixa de alvo.

Cada card de FC/potência/cadência mostra a **média da etapa atual** embaixo do valor instantâneo, e ganha uma borda colorida + seta (▲ subir / ▼ baixar / ● no alvo) quando a etapa tem um alvo definido — verde quando dentro da faixa, laranja/vermelho quando fora. Se um sensor desconectar, um banner aparece no topo com um botão **Reconectar agora** (pede pro listener BLE tentar de novo na hora, sem esperar o timer normal de retry).

Ao terminar, clique **Exportar .tcx** (upload manual em Garmin Connect ou TrainingPeaks) ou **Exportar .xlsx** (planilha com 3 gráficos: FC, potência e cadência x tempo).

## Arquitetura
- `workout_parser.py` — lê o `.fit` do treino planejado: extrai etapas, converte alvo de FC/potência (ver nota sobre offsets abaixo) e expande blocos de repetição ("repetir 5x") em etapas individuais
- `state.py` — estado em memória: sensores + cronômetro + etapa atual + **pausa/retomada/encerramento manual** + gravação contínua de amostras (`record_sample()`, chamado a cada tick, pulado enquanto pausado)
- `ble_parsers.py` — decodificação pura dos bytes GATT (testada com payloads sintéticos)
- `ble_sensor_listener.py` — scan BLE, conecta na cinta e no rolo, alimenta o estado; reconecta sozinho (rescan completo, cancelando tasks órfãs) se qualquer sensor cair no meio do treino
- `tcx_export.py` — converte as amostras em `.tcx`, com um Lap por etapa do treino, FC e cadência nos campos padrão TCX, potência via extensão oficial Garmin (`ActivityExtension/v2`, `TPX/Watts`)
- `xlsx_export.py` — planilha com aba de dados brutos + 3 gráficos de linha (openpyxl)
- `server.py` — Flask (sem WebSocket) + rotas `/snapshot`, `/next_step`, `/start`, `/pause`, `/resume`, `/stop`, `/reconnect`, `/export/tcx` e `/export/xlsx`; checa se a porta 5000 já está em uso *antes* de gravar `dashboard.pid` (evita órfãos zumbis — ver nota abaixo); redireciona stdout/stderr pra `dashboard.log` quando rodando sem console (via `pythonw.exe`)
- `templates/index.html` — dashboard fullscreen com sidebar de próximas etapas, médias por etapa, indicador visual de alvo (acima/dentro/abaixo) e banner de conexão BLE; faz polling em `/snapshot` a cada 1s via `fetch`, com o cronômetro interpolado localmente a cada 250ms pra não saltar se o poll atrasar
- `inspect_fit_steps.py` — diagnóstico: dump cru de todos os campos de um `.fit` real, usado pra descobrir/confirmar como o Garmin Connect codifica cada tipo de alvo
- `tools/ble_scan_check.py` — smoke test isolado de scan BLE (rode antes do `server.py` pra confirmar que o rolo/cinta aparecem com o serviço certo)
- `IniciarDashboard.vbs` / `PararDashboard.vbs` — launchers pro Windows, sem janela de terminal

### Por que polling e não WebSocket
Um update por segundo não justifica o custo de Flask-SocketIO (que traz `python-socketio` + `eventlet`/`gevent` como dependências do servidor) nem de carregar a lib Socket.IO de um CDN externo — o que, sem internet na sala de treino, quebraria o dashboard inteiro. `fetch('/snapshot')` a cada 1s cobre a mesma necessidade com zero dependências extras e funciona 100% offline.

### Decodificação de alvo de FC/potência — confirmada com dado real
O `.fit` real do Garmin Connect usa campos específicos por tipo de alvo, não um campo genérico:
- **FC**: `custom_target_heart_rate_low/high`, com offset de **+100** sobre o valor bruto (`raw_value=273` → 173 bpm real).
- **Potência**: `custom_target_power_low/high`, offset de **+1000** (`raw_value=1087` → 87W real; `raw_value=1000` → 0W, sentinela de "sem limite inferior").
- Em ambos, o valor decodificado (`.value`) do `fitparse` pode vir como **string** sentinela (`'bpm_offset'`/`'watts_offset'`) em vez de número — por isso o parser sempre lê `.raw_value` (sempre numérico) pra esses 4 campos.
- Zona pré-definida (`target_hr_zone`/`target_power_zone`) tem prioridade sobre o range customizado, se estiver setada (!= 0).
- **Confirmado com `.fit` real** (ver `Treino.fit` de exemplo): 12 mensagens brutas → 22 etapas expandidas, 2 blocos de repetição (4x cada) sem se confundir entre si, offsets de potência batendo exatamente com os valores mostrados pelo Garmin Connect.

**Ainda não verificado com dado real**: alvo de cadência/velocidade (`custom_target_cadence_*`, `custom_target_speed_*`) — lidos sem offset, hipótese não testada porque nenhum `.fit` real testado até agora tinha esse tipo de alvo. Rode `inspect_fit_steps.py` nesse arquivo antes de confiar no valor exibido, se aparecer.

**Limitação conhecida**: repetição aninhada (bloco de repeat dentro de outro) não é tratada — não observada em nenhum `.fit` real testado.

### Reconexão BLE e porta ocupada
O listener BLE reconecta sozinho (rescan completo) se um sensor cair, e o botão **Reconectar agora** da UI pula a espera normal de 5s pra tentar na hora — sinalizado via um `threading.Event`, thread-safe entre a requisição Flask e o loop asyncio do listener. Já em produção real: o servidor confirmou conectar no rolo (EliteTrainer, via CSC Measurement) sem cair, num teste em Windows.

`server.py` também checa se a porta 5000 já está em uso **antes** de gravar `dashboard.pid` — sem isso, um servidor zumbi anterior (travado, sem ninguém pra apertar Ctrl+C) faria uma nova tentativa de start morrer em silêncio (bind falha) mas ainda sobrescrever o `dashboard.pid` com o PID do processo novo (que já morreu), deixando o zumbi órfão e impossível de matar pelo `PararDashboard.vbs`. Isso já aconteceu na prática.

## O que foi validado
- **Parser de `.fit` real**: testado ponta a ponta contra um `.fit` real do usuário — expansão de repeats e offsets de FC/potência conferidos e corretos.
- **Pausar/Retomar/Encerrar**: testado com sleeps reais — tempo total e da etapa ficam congelados durante a pausa, etapas não avançam enquanto pausado, `/resume` não destrava mais um treino já encerrado.
- **Médias por etapa e indicador de alvo**: testado que a média ignora leituras 0 (sensor sem dado ainda) e zera a cada avanço de etapa; testado `target_status` (below/in_range/above) contra os três casos.
- **TCX/XLSX**: gerados a partir de amostras reais do treino de exemplo, XML bem formado, 3 gráficos presentes na aba "Gráficos".
- **Endpoints Flask** (`/snapshot`, `/next_step`, `/pause`, `/resume`, `/stop`, `/reconnect`, `/export/tcx`, `/export/xlsx`): testados via `test_client()`, incluindo o caso de erro (exportar sem amostras → 400 com mensagem, não 500 genérico) e a checagem de porta ocupada (servidor recusa subir com uma mensagem clara, sem sobrescrever o PID de um processo zumbi).
- **Conectado de verdade com hardware** (log real do usuário, Windows): rolo EliteTrainer via CSC Measurement, sem erros no `dashboard.log`, launcher `IniciarDashboard.vbs` funcionando (rodou via `pythonw.exe` sem console).
- **Não testado neste ambiente** (Linux, sem hardware): a interpolação do cronômetro no navegador (`renderClock()`) e o banner de reconexão foram revisados por leitura de código, não em navegador real.

## Próximos passos naturais
- Agrupamento visual de blocos de repetição na sidebar (hoje aparece como lista plana — cada repetição é uma entrada separada, sem o "Repetir 4x" do TrainingPeaks)
- Confirmar alvo de cadência/velocidade com um `.fit` real que tenha esse tipo de etapa
