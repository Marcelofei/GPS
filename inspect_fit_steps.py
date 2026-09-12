"""
Diagnóstico: mostra TODOS os campos crus de cada workout_step do seu .fit
real, sem nenhuma interpretação — só pra descobrirmos exatamente como o
Garmin Connect codifica alvo de potência (watts absolutos? %FTP? zona?)
antes de implementar a extração definitiva no workout_parser.py.

Uso:
    python inspect_fit_steps.py caminho/do/treino.fit

Rode isso no seu treino real (o que tem a etapa "Girando rápido" com alvo
de potência) e me mande a saída completa.
"""
import sys
import fitparse


def main(path: str):
    fitfile = fitparse.FitFile(path)

    print("=" * 70)
    print("MENSAGENS 'workout' (metadados gerais do treino, se houver)")
    print("=" * 70)
    for msg in fitfile.get_messages("workout"):
        for f in msg:
            print(f"  {f.name} = {f.value!r}  (unidades: {f.units!r})")
        print()

    print("=" * 70)
    print("MENSAGENS 'workout_step' (uma por etapa)")
    print("=" * 70)
    for i, msg in enumerate(fitfile.get_messages("workout_step")):
        print(f"\n--- step índice {i} ---")
        for f in msg:
            print(f"  {f.name} = {f.value!r}  (unidades: {f.units!r}, raw_value={f.raw_value!r})")

    print()
    print("=" * 70)
    print("Copie TODA essa saída (do início ao fim) e me envie.")
    print("Preste atenção especial na(s) etapa(s) que você sabe que tem")
    print("alvo de potência definido (ex: 'Girando rápido') — quero ver")
    print("exatamente os campos target_type, target_value,")
    print("custom_target_value_low/high (ou variantes) dessa etapa.")
    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python inspect_fit_steps.py caminho/do/treino.fit")
        sys.exit(1)
    main(sys.argv[1])
