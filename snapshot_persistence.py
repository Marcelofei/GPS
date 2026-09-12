"""
Persistência incremental do estado do treino em disco — sobrevive a um
kill do processo (Task Manager, queda de energia, crash) que não passa
pelo /shutdown normal.

Escrita ATÔMICA: grava num arquivo temporário e só depois renomeia pro
nome final (os.replace é atômico no mesmo filesystem, em Windows e Linux)
— se o processo morrer no meio da escrita, o arquivo antigo (ou nenhum)
continua íntegro, nunca um JSON pela metade e corrompido.

Localização: tempfile.gettempdir() — mesmo padrão já usado no resto do
projeto por causa do bug de "/tmp não existe no Windows" já corrigido.
"""
import json
import os
import tempfile

SNAPSHOT_FILENAME = "dashboard_state_snapshot.json"


def _snapshot_path() -> str:
    return os.path.join(tempfile.gettempdir(), SNAPSHOT_FILENAME)


def save_snapshot(data: dict) -> None:
    """Grava o snapshot atomicamente. Falhas de escrita (disco cheio, etc)
    não devem derrubar o treino em andamento — quem chama decide se loga
    ou ignora, esta função deixa a exceção subir pra isso ficar explícito."""
    path = _snapshot_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)  # atômico


def load_snapshot() -> dict | None:
    """Lê o snapshot em disco, se existir e for um JSON válido. Um arquivo
    corrompido (não deveria acontecer, dado o replace atômico, mas por
    segurança) é tratado como 'nenhum snapshot', não como erro fatal."""
    path = _snapshot_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def clear_snapshot() -> None:
    """Remove o snapshot — chamado depois de um export bem-sucedido (manual
    ou automático no shutdown), pra não ficar oferecendo recuperação de
    uma sessão que já foi exportada."""
    path = _snapshot_path()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
