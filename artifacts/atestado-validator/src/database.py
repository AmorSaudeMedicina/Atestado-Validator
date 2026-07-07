"""
Camada de acesso ao banco de dados SQLite.

Guarda os atestados emitidos de forma persistente entre sessões.
Banco criado automaticamente em data/atestados.db na primeira execução.
"""

import sqlite3
from pathlib import Path
from typing import Optional

# Caminho absoluto baseado na localização deste arquivo, sobe um nível até a raiz do projeto
_DB_DIR = Path(__file__).resolve().parent.parent / "data"
_DB_PATH = _DB_DIR / "atestados.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS atestados (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo           TEXT    UNIQUE NOT NULL,
    nome_medico      TEXT    NOT NULL,
    crm              TEXT    NOT NULL,
    nome_paciente    TEXT    NOT NULL,
    cid              TEXT    NOT NULL,
    data_emissao     TEXT    NOT NULL,
    data_inicio      TEXT,
    data_fim         TEXT,
    dias_afastamento INTEGER,
    criado_em        TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
)
"""


def _conectar() -> sqlite3.Connection:
    """
    Abre uma conexão nova por chamada (sem conexão compartilhada entre threads).
    WAL mode permite leituras simultâneas sem bloquear escritas.
    timeout=10 evita erros imediatos de 'database is locked' sob carga leve.
    """
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db() -> None:
    """Cria as tabelas se ainda não existirem."""
    with _conectar() as conn:
        conn.execute(_CREATE_TABLE)
        conn.commit()


def salvar_atestado(
    codigo: str,
    nome_medico: str,
    crm: str,
    nome_paciente: str,
    cid: str,
    data_emissao: str,
    data_inicio: Optional[str],
    data_fim: Optional[str],
    dias_afastamento: Optional[int],
) -> None:
    """Persiste um novo atestado no banco."""
    sql = """
        INSERT INTO atestados
            (codigo, nome_medico, crm, nome_paciente, cid,
             data_emissao, data_inicio, data_fim, dias_afastamento)
        VALUES (?,?,?,?,?,?,?,?,?)
    """
    with _conectar() as conn:
        conn.execute(
            sql,
            (codigo, nome_medico, crm, nome_paciente, cid,
             data_emissao, data_inicio, data_fim, dias_afastamento),
        )
        conn.commit()


def buscar_atestado_por_codigo(codigo: str) -> Optional[dict]:
    """Retorna os dados do atestado ou None se não encontrado."""
    sql = "SELECT * FROM atestados WHERE codigo = ?"
    with _conectar() as conn:
        row = conn.execute(sql, (codigo,)).fetchone()
    return dict(row) if row else None


def listar_atestados_por_crm(crm: str) -> list[dict]:
    """Retorna todos os atestados emitidos por um médico (mais recentes primeiro)."""
    sql = "SELECT * FROM atestados WHERE crm = ? ORDER BY id DESC"
    with _conectar() as conn:
        rows = conn.execute(sql, (crm,)).fetchall()
    return [dict(r) for r in rows]
