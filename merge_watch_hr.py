"""
Mescla a FC gravada pelo RELÓGIO (mensagens 'record' de um .fit de
atividade gravada) com potência/cadência capturadas pelo DASHBOARD
(exportadas via /export/samples_json), gerando um .tcx único pronto pra
subir no Garmin Connect ou TrainingPeaks.

Por que .tcx e não .fit na saída: o projeto já tem um gerador de .tcx
testado e validado (tcx_export.py) — reaproveitado aqui sem mudança.
Gerar um .fit binário do zero exigiria uma biblioteca de encoding própria
(fitparse só LÊ .fit, não escreve), então .tcx é o caminho mais confiável
com o que já está testado no projeto. .tcx já é aceito normalmente por
Garmin Connect e TrainingPeaks — funcionalmente resolve o mesmo problema.

Alinhamento de tempo: casa cada amostra do dashboard com o record mais
próximo no tempo do relógio, usando o RELÓGIO DE PAROS DO PC (wall_time,
já gravado em cada amostra do dashboard) contra o timestamp absoluto do
.fit do relógio. Se os relógios (PC e relógio de pulso) estiverem
dessincronizados por alguns segundos, use --offset-seconds pra corrigir
manualmente (positivo = atrasa o relógio do pulso em relação ao PC).

Uso:
    python merge_watch_hr.py atividade_do_relogio.fit treino_samples.json saida.tcx [--offset-seconds N] [--max-gap-seconds N]

Onde:
    atividade_do_relogio.fit — exportado do Garmin Connect (atividade JÁ
        GRAVADA, com dados reais — não o .fit de treino planejado)
    treino_samples.json — baixado do dashboard via botão/rota
        /export/samples_json, ANTES de rodar o export normal (que limpa
        o snapshot)
    saida.tcx — nome do arquivo final a subir no Garmin Connect/TrainingPeaks
"""
import sys
import json
import argparse
import fitparse
from datetime import timezone

from tcx_export import export_tcx_to_file


def _load_watch_hr_records(fit_path: str) -> list[tuple[float, int]]:
    """Lê as mensagens 'record' do .fit de atividade do relógio, devolve
    lista de (timestamp_unix, heart_rate), só das que têm FC presente.

    IMPORTANTE (bug real já corrigido uma vez): o fitparse decodifica o
    campo 'timestamp' com `datetime.utcfromtimestamp(...)`, que devolve um
    datetime SEM fuso horário anexado (naive) — mesmo o valor sendo UTC de
    verdade. Chamar `.timestamp()` direto nesse valor faz o Python tratar
    como HORA LOCAL, introduzindo um erro de horas inteiras (o offset UTC
    do fuso do usuário), não segundos. Por isso o `.replace(tzinfo=utc)`
    explícito abaixo é obrigatório, não cosmético — sem ele, a mesclagem
    falha silenciosamente pra 0% em qualquer fuso diferente de UTC."""
    fitfile = fitparse.FitFile(fit_path)
    records = []
    for msg in fitfile.get_messages("record"):
        fields = {f.name: f.value for f in msg}
        ts = fields.get("timestamp")
        hr = fields.get("heart_rate")
        if ts is None or hr is None:
            continue
        ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        records.append((ts_utc.timestamp(), int(hr)))
    records.sort(key=lambda r: r[0])
    return records


def _nearest_hr(records: list[tuple[float, int]], target_ts: float, max_gap_seconds: float) -> int | None:
    """Busca binária simples pelo record de FC mais próximo no tempo.
    Devolve None se o mais próximo ainda estiver mais longe que
    max_gap_seconds (evita inventar FC de um instante muito distante)."""
    if not records:
        return None

    lo, hi = 0, len(records) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if records[mid][0] < target_ts:
            lo = mid + 1
        else:
            hi = mid

    candidates = [records[lo]]
    if lo > 0:
        candidates.append(records[lo - 1])

    best = min(candidates, key=lambda r: abs(r[0] - target_ts))
    if abs(best[0] - target_ts) > max_gap_seconds:
        return None
    return best[1]


def merge(fit_path: str, samples_json_path: str, out_path: str,
          offset_seconds: float = 0.0, max_gap_seconds: float = 5.0) -> dict:
    """Faz a mesclagem e escreve o .tcx final. Devolve um dict com
    estatísticas (quantas amostras conseguiram FC, quantas ficaram sem)
    pra dar visibilidade real do resultado, não só 'deu certo silenciosamente'."""
    watch_records = _load_watch_hr_records(fit_path)
    if not watch_records:
        raise ValueError(
            "Nenhum record com heart_rate encontrado no .fit do relógio. "
            "Confirme que é o .fit de uma ATIVIDADE GRAVADA (com dados reais), "
            "não um .fit de treino planejado."
        )

    with open(samples_json_path, "r", encoding="utf-8") as f:
        dashboard_samples = json.load(f)

    if not dashboard_samples:
        raise ValueError("O arquivo de amostras do dashboard está vazio.")

    matched = 0
    unmatched = 0
    merged_samples = []
    for s in dashboard_samples:
        s = dict(s)  # não mutar o original
        target_ts = s["wall_time"] + offset_seconds
        hr = _nearest_hr(watch_records, target_ts, max_gap_seconds)
        if hr is not None:
            s["heart_rate"] = hr
            matched += 1
        else:
            unmatched += 1
        merged_samples.append(s)

    export_tcx_to_file(merged_samples, out_path)

    return {
        "total_samples": len(merged_samples),
        "matched_with_hr": matched,
        "unmatched": unmatched,
        "watch_records_available": len(watch_records),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fit_path", help="Caminho do .fit de atividade gravada pelo relógio")
    parser.add_argument("samples_json_path", help="Caminho do treino_samples.json exportado do dashboard")
    parser.add_argument("out_path", help="Caminho do .tcx final a gerar")
    parser.add_argument("--offset-seconds", type=float, default=0.0,
                         help="Correção manual se os relógios (PC e pulso) estiverem dessincronizados")
    parser.add_argument("--max-gap-seconds", type=float, default=5.0,
                         help="Distância máxima aceitável (segundos) entre uma amostra e o record de FC mais próximo")
    args = parser.parse_args()

    try:
        stats = merge(args.fit_path, args.samples_json_path, args.out_path,
                       offset_seconds=args.offset_seconds, max_gap_seconds=args.max_gap_seconds)
    except (ValueError, FileNotFoundError) as e:
        print(f"ERRO: {e}")
        sys.exit(1)

    print(f"Mesclagem concluída: {args.out_path}")
    print(f"  Total de amostras do dashboard: {stats['total_samples']}")
    print(f"  Conseguiram FC do relógio: {stats['matched_with_hr']}")
    print(f"  Ficaram SEM FC (fora da tolerância de {args.max_gap_seconds}s): {stats['unmatched']}")
    print(f"  Records de FC disponíveis no .fit do relógio: {stats['watch_records_available']}")
    if stats["unmatched"] > 0:
        print()
        print("Se muitas amostras ficaram sem FC, os relógios (PC e pulso) podem")
        print("estar dessincronizados — tente de novo com --offset-seconds "
              "(positivo ou negativo) pra ajustar.")
