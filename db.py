"""
Camada de dados do ClaroUnify Hub — SQLite.

Substitui os dicionários hardcoded (CRM_DB, BSS_DB, OSS_DB, CATALOGO) por tabelas
reais num banco de arquivo único (`clarounify.db`, criado automaticamente na
primeira execução). Continua sendo "mock" no sentido de que os dados são fictícios
e não vêm de um CRM/BSS/OSS real da Claro — mas agora estão numa camada de
persistência de verdade, não presos no código Python.

Por que SQLite e não Postgres/Redis aqui: é nativo do Python (nenhuma dependência
nova, nenhum serviço externo pra subir), e para o volume de um MVP de demonstração
é mais que suficiente. A troca para Postgres em produção seria só trocar a função
`get_db()` por uma conexão de pool real — as queries (SQL padrão) continuam
funcionando quase sem alteração.
"""

import os
import sqlite3
import time
import uuid
from collections.abc import MutableMapping
from pathlib import Path
from typing import Optional

# Configurável via DATABASE_PATH — importante para apontar a um volume
# persistente no Docker (ver docker-compose.yml), em vez de gravar dentro do
# container e perder tudo ao recriar a imagem.
DB_PATH = Path(os.environ.get("DATABASE_PATH", str(Path(__file__).parent / "clarounify.db")))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Cria as tabelas se não existirem e popula com dados mock na primeira vez.
    Chamado uma vez na subida do servidor — idempotente, seguro de rodar de novo
    (não duplica dados se o banco já existir)."""
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id             TEXT PRIMARY KEY,
            nome           TEXT NOT NULL,
            plano          TEXT NOT NULL,
            cliente_desde  TEXT NOT NULL,
            nps_historico  INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS invoices (
            customer_id TEXT PRIMARY KEY REFERENCES customers(id),
            valor       REAL NOT NULL,
            vencimento  TEXT NOT NULL,
            status      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS network_status (
            customer_id           TEXT PRIMARY KEY REFERENCES customers(id),
            status_rede           TEXT NOT NULL,
            regiao                TEXT NOT NULL,
            previsao_normalizacao TEXT
        );

        CREATE TABLE IF NOT EXISTS catalog (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            nome      TEXT NOT NULL,
            preco     REAL NOT NULL,
            descricao TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            customer_id        TEXT PRIMARY KEY,
            trace_id           TEXT NOT NULL,
            status             TEXT NOT NULL DEFAULT 'bot',
            current_agent      TEXT,
            last_intent        TEXT,
            created_at         REAL NOT NULL,
            updated_at         REAL NOT NULL,
            nps                INTEGER,
            unclear_count      INTEGER NOT NULL DEFAULT 0,
            consent_given      INTEGER NOT NULL DEFAULT 0,
            frustration_score  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL REFERENCES sessions(customer_id),
            author      TEXT NOT NULL,
            channel     TEXT NOT NULL,
            text        TEXT NOT NULL,
            ts          REAL NOT NULL
        );
        """
    )
    conn.commit()

    already_seeded = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] > 0
    if not already_seeded:
        _seed(conn)
    conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    # 20 clientes com nomes, planos e status variados — o suficiente para busca,
    # filtro por segmento e paginação fazerem sentido na demo (numa Claro real
    # seriam milhões, mas o padrão de acesso é o mesmo).
    nomes = [
        "João Pereira", "Mariana Souza", "Carlos Andrade", "Beatriz Lima",
        "Rafael Nogueira", "Fernanda Costa", "Lucas Martins", "Juliana Alves",
        "Pedro Henrique Rocha", "Camila Ribeiro", "Diego Fernandes", "Larissa Melo",
        "Bruno Carvalho", "Patrícia Gomes", "Thiago Barbosa", "Renata Cardoso",
        "Vinícius Teixeira", "Aline Moreira", "Gustavo Pinto", "Débora Castro",
    ]
    planos = ["Claro Pós 20GB", "Claro Fibra 300MB", "Claro Pós 40GB",
              "Claro Controle 15GB", "Claro Fibra 500MB", "Claro Pós 80GB"]
    fatura_status_ciclo = ["em_aberto", "paga", "em_aberto", "atrasada", "paga"]
    rede_ciclo = ["instabilidade", "normal", "normal", "normal", "instabilidade", "normal"]
    regioes = ["CTO-114 (Zona Leste)", "CTO-052 (Zona Sul)", "CTO-030 (Centro)",
               "CTO-201 (Zona Norte)", "CTO-077 (Zona Oeste)", "CTO-098 (ABC)"]

    customers, invoices, network = [], [], []
    for i, nome in enumerate(nomes, start=1):
        cid = f"1199999{i:04d}"
        customers.append((cid, nome, planos[i % len(planos)], f"20{19 + i % 6}-0{1 + i % 9}-1{i % 9}", 4 + i % 6))
        fstatus = fatura_status_ciclo[i % len(fatura_status_ciclo)]
        invoices.append((cid, round(59.90 + (i % 8) * 10, 2), f"2026-09-{(i % 27) + 1:02d}", fstatus))
        rstatus = rede_ciclo[i % len(rede_ciclo)]
        network.append((cid, rstatus, regioes[i % len(regioes)], "2h" if rstatus == "instabilidade" else None))

    conn.executemany("INSERT INTO customers (id, nome, plano, cliente_desde, nps_historico) VALUES (?,?,?,?,?)", customers)
    conn.executemany("INSERT INTO invoices (customer_id, valor, vencimento, status) VALUES (?,?,?,?)", invoices)
    conn.executemany("INSERT INTO network_status (customer_id, status_rede, regiao, previsao_normalizacao) VALUES (?,?,?,?)", network)
    conn.executemany(
        "INSERT INTO catalog (nome, preco, descricao) VALUES (?,?,?)",
        [
            ("Claro Pós 40GB", 79.90, "40GB + apps ilimitados"),
            ("Claro Pós 80GB", 99.90, "80GB + apps ilimitados + roaming LATAM"),
            ("Claro Fibra 500MB", 129.90, "500MB fibra + Wi-Fi 6 incluso"),
        ],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Funções de acesso (repositório) — cada uma abre e fecha sua própria conexão.
# Para o volume de uma demo isso é simples e seguro; em produção viraria um
# pool de conexões reaproveitado entre requisições.
# ---------------------------------------------------------------------------

def get_customer_record(customer_id: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_customers(busca: str = "", fatura_status: str = "", status_rede: str = "",
                    limit: int = 20, offset: int = 0) -> dict:
    """Busca paginada e filtrável — numa base com milhões de clientes (Claro real),
    listar tudo de uma vez não é uma opção; o padrão de acesso tem que ser sempre
    'busca por segmento + paginação', nunca um dump completo da tabela."""
    where, params = [], []
    if busca:
        where.append("(c.nome LIKE ? OR c.id LIKE ?)")
        params += [f"%{busca}%", f"%{busca}%"]
    if fatura_status:
        where.append("i.status = ?")
        params.append(fatura_status)
    if status_rede:
        where.append("n.status_rede = ?")
        params.append(status_rede)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_db()
    total = conn.execute(
        f"""SELECT COUNT(*) FROM customers c
            LEFT JOIN invoices i ON i.customer_id = c.id
            LEFT JOIN network_status n ON n.customer_id = c.id
            {where_sql}""",
        params,
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT c.*, i.valor AS fatura_valor, i.vencimento AS fatura_vencimento,
                   i.status AS fatura_status, n.status_rede
            FROM customers c
            LEFT JOIN invoices i ON i.customer_id = c.id
            LEFT JOIN network_status n ON n.customer_id = c.id
            {where_sql}
            ORDER BY c.nome
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return {"total": total, "items": [dict(r) for r in rows]}


def list_customer_ids_matching(busca: str = "", fatura_status: str = "", status_rede: str = "") -> list[str]:
    """Igual list_customers, mas devolve TODOS os IDs que batem com o filtro, sem
    paginação — usado para ação em massa (contatar todos os clientes de um
    segmento, ex.: 'todos com fatura atrasada'), não para exibir numa tela."""
    where, params = [], []
    if busca:
        where.append("(c.nome LIKE ? OR c.id LIKE ?)")
        params += [f"%{busca}%", f"%{busca}%"]
    if fatura_status:
        where.append("i.status = ?")
        params.append(fatura_status)
    if status_rede:
        where.append("n.status_rede = ?")
        params.append(status_rede)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_db()
    rows = conn.execute(
        f"""SELECT c.id FROM customers c
            LEFT JOIN invoices i ON i.customer_id = c.id
            LEFT JOIN network_status n ON n.customer_id = c.id
            {where_sql}""",
        params,
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def get_invoice(customer_id: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT customer_id, valor AS fatura_valor, vencimento, status FROM invoices WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_network_status(customer_id: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM network_status WHERE customer_id = ?", (customer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_catalog() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT nome, preco, descricao FROM catalog").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Context Manager persistido — substitui o dicionário `sessions` em memória.
# ---------------------------------------------------------------------------
#
# `Session` se comporta como um dicionário Python comum: `session["status"] =
# "handoff"` já grava direto na tabela `sessions`; `session["history"].append(...)`
# grava direto na tabela `messages`. Isso significa que TODO o resto do código em
# main.py que já lia/escrevia `session[...]` continua funcionando sem alteração —
# só a implementação por baixo mudou de memória para persistência real em disco.
# Uma reinicialização do servidor (ou uma queda) não perde mais o contexto do
# cliente, que é exatamente o que RF002 (Memória Contextual de Sessão) pede.

SESSION_FIELDS = {
    "trace_id", "status", "current_agent", "last_intent", "created_at",
    "updated_at", "nps", "unclear_count", "consent_given", "frustration_score",
}


class SessionHistory(list):
    """Histórico de mensagens de uma sessão. Populada com o estado atual do banco
    no momento da leitura; `.append()` grava a mensagem na tabela `messages` na
    hora — não existe um passo separado de "salvar", é como usar uma lista comum."""

    def __init__(self, customer_id: str, rows: list[dict]):
        super().__init__(rows)
        self.customer_id = customer_id

    def append(self, entry: dict) -> None:
        super().append(entry)
        conn = get_db()
        conn.execute(
            "INSERT INTO messages (customer_id, author, channel, text, ts) VALUES (?,?,?,?,?)",
            (self.customer_id, entry["author"], entry["channel"], entry["text"], entry["ts"]),
        )
        conn.commit()
        conn.close()


class Session(MutableMapping):
    """Sessão de conversa persistida em SQLite, com a mesma interface de um
    dicionário Python. Só os campos em SESSION_FIELDS (mais 'customer_id' e
    'history') existem — qualquer outra chave levanta KeyError, do mesmo jeito
    que aconteceria com um dict comum que nunca teve aquele campo."""

    def __init__(self, customer_id: str):
        self.customer_id = customer_id

    def _row(self) -> Optional[sqlite3.Row]:
        conn = get_db()
        row = conn.execute("SELECT * FROM sessions WHERE customer_id=?", (self.customer_id,)).fetchone()
        conn.close()
        return row

    def __getitem__(self, key):
        if key == "customer_id":
            return self.customer_id
        if key == "history":
            conn = get_db()
            rows = conn.execute(
                "SELECT author, channel, text, ts FROM messages WHERE customer_id=? ORDER BY ts, id",
                (self.customer_id,),
            ).fetchall()
            conn.close()
            return SessionHistory(self.customer_id, [dict(r) for r in rows])
        if key not in SESSION_FIELDS:
            raise KeyError(key)
        row = self._row()
        if row is None:
            raise KeyError(key)
        value = row[key]
        return bool(value) if key == "consent_given" else value

    def __setitem__(self, key, value):
        if key in ("customer_id", "history"):
            raise TypeError(f"'{key}' não pode ser atribuído diretamente")
        if key not in SESSION_FIELDS:
            raise KeyError(key)
        if key == "consent_given":
            value = int(bool(value))
        conn = get_db()
        # key vem sempre de uma whitelist fixa (SESSION_FIELDS), nunca de entrada
        # do usuário — seguro compor no SQL apesar de não ser um parâmetro "?".
        conn.execute(f"UPDATE sessions SET {key} = ? WHERE customer_id = ?", (value, self.customer_id))
        conn.commit()
        conn.close()

    def __delitem__(self, key):
        raise TypeError("remoção de campo de sessão não é suportada")

    def __iter__(self):
        return iter(["customer_id", "history", *SESSION_FIELDS])

    def __len__(self):
        return len(SESSION_FIELDS) + 2

    def __repr__(self):
        return f"<Session {self.customer_id}>"


def session_exists(customer_id: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM sessions WHERE customer_id=?", (customer_id,)).fetchone()
    conn.close()
    return row is not None


def get_or_create_session(customer_id: str) -> Session:
    if not session_exists(customer_id):
        now = time.time()
        conn = get_db()
        conn.execute(
            """INSERT INTO sessions
               (customer_id, trace_id, status, current_agent, last_intent,
                created_at, updated_at, nps, unclear_count, consent_given, frustration_score)
               VALUES (?, ?, 'bot', NULL, NULL, ?, ?, NULL, 0, 0, 0)""",
            (customer_id, uuid.uuid4().hex[:12], now, now),
        )
        conn.commit()
        conn.close()
    return Session(customer_id)


def list_sessions() -> list[Session]:
    conn = get_db()
    rows = conn.execute("SELECT customer_id FROM sessions").fetchall()
    conn.close()
    return [Session(r["customer_id"]) for r in rows]


def get_trace_id(customer_id: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute("SELECT trace_id FROM sessions WHERE customer_id=?", (customer_id,)).fetchone()
    conn.close()
    return row["trace_id"] if row else None


def delete_session(customer_id: str) -> None:
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE customer_id=?", (customer_id,))
    conn.execute("DELETE FROM sessions WHERE customer_id=?", (customer_id,))
    conn.commit()
    conn.close()


def reset_all_sessions() -> None:
    conn = get_db()
    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM sessions")
    conn.commit()
    conn.close()
