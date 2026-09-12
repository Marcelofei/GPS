"""
Diagnóstico: mostra os campos crus das mensagens 'record' de um .fit de
ATIVIDADE GRAVADA (o que o relógio salva sozinho durante o treino — não
o .fit de treino PLANEJADO que o resto do projeto usa).

'record' é a mensagem que se repete a cada poucos segundos durante uma
atividade, contendo os dados instantâneos (FC, posição, velocidade, etc).
Isso é diferente do 'workout_step' que já mexemos bastante — são tipos de
mensagem completamente diferentes dentro do formato .fit.

Uso:
    python inspect_fit_records.py caminho/da/atividade.fit

Rode isso no .fit exportado do Garmin Connect da atividade que você
gravou no relógio durante o treino, e me manda a saída (só as primeiras
linhas já bastam, não precisa mandar tudo).
"""
import sys
import fitparse


def main(path: str):
    fitfile = fitparse.FitFile(path)

    count = 0
    print("=" * 70)
    print("Primeiras 5 mensagens 'record' encontradas:")
    print("=" * 70)
    for msg in fitfile.get_messages("record"):
        count += 1
        print(f"\n--- record #{count} ---")
        for f in msg:
            print(f"  {f.name} = {f.value!r}  (unidades: {f.units!r})")
        if count >= 5:
            break

    if count == 0:
        print("\nNENHUMA mensagem 'record' encontrada neste arquivo.")
        print("Confirme que esse é o .fit de uma ATIVIDADE GRAVADA (com dados)")
        print("e não um .fit de treino PLANEJADO (que só tem 'workout_step').")
        return

    print()
    print("=" * 70)
    print(f"Total de mensagens 'record' no arquivo: {sum(1 for _ in fitfile.get_messages('record'))}")
    print("Confira acima se aparecem os campos 'timestamp' e 'heart_rate'")
    print("exatamente com esses nomes — é isso que a mesclagem vai usar.")
    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python inspect_fit_records.py caminho/da/atividade.fit")
        sys.exit(1)
    main(sys.argv[1])
