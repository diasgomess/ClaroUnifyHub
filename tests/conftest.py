"""
Fixtures compartilhadas da suíte.

Decisões:
- USE_LLM=false SEMPRE nos testes: a suíte não pode depender de o Ollama estar
  rodando na máquina de quem roda `pytest` (nem no CI). Isso testa o caminho
  determinístico (fallback por palavras-chave), que é o que garante que o
  sistema nunca quebra — o caminho do LLM é validado manualmente/no vídeo e
  pela suíte prova_dos_9.py contra um servidor real.
- Banco isolado por teste (arquivo temporário via tmp_path), para que um teste
  nunca veja dado deixado por outro.
"""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test_clarounify.db"))
    monkeypatch.setenv("USE_LLM", "false")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    # Recarrega os módulos para que peguem as env vars acima (DB_PATH é lido
    # no import de db.py, no nível do módulo).
    import db as db_module
    import main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)

    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture()
def cliente_id():
    """Cliente de teste que já existe no seed de dados (ver db.py)."""
    return "11999990001"
