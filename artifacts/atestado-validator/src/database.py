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
    status           TEXT    NOT NULL DEFAULT 'ativo',
    revogado_em      TEXT,
    criado_em        TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
)
"""

# Colunas adicionadas depois da criação inicial do banco. Cada entrada é
# aplicada via ALTER TABLE apenas se a coluna ainda não existir, para nunca
# apagar ou recriar os registros já existentes — eles simplesmente passam a
# ter os novos campos com o valor padrão (status='ativo', revogado_em=NULL).
_MIGRACOES_COLUNAS = [
    ("status", "TEXT NOT NULL DEFAULT 'ativo'"),
    ("revogado_em", "TEXT"),
]


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
    """
    Cria as tabelas se ainda não existirem e aplica migrações de colunas novas.

    A migração usa ALTER TABLE ADD COLUMN — nunca DROP/CREATE — então atestados
    já gravados permanecem intactos e simplesmente herdam os valores padrão
    das colunas novas (status='ativo', revogado_em=NULL).
    """
    with _conectar() as conn:
        conn.execute(_CREATE_TABLE)
        colunas_existentes = {
            linha["name"] for linha in conn.execute("PRAGMA table_info(atestados)")
        }
        for nome_coluna, definicao_sql in _MIGRACOES_COLUNAS:
            if nome_coluna not in colunas_existentes:
                conn.execute(f"ALTER TABLE atestados ADD COLUMN {nome_coluna} {definicao_sql}")
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


def revogar_atestado(codigo: str, crm: str) -> bool:
    """
    Marca um atestado como 'revogado' com a data/hora atual.

    Só tem efeito se o atestado existir, pertencer ao médico informado (mesmo
    `crm`) e ainda estiver 'ativo' — isso impede que um médico revogue
    atestados de outro colega e evita sobrescrever a data de uma revogação
    já feita. Retorna True se o atestado foi revogado agora, False caso
    contrário (não encontrado, não pertence a esse CRM, ou já revogado).
    """
    sql = """
        UPDATE atestados
        SET status = 'revogado', revogado_em = datetime('now','localtime')
        WHERE codigo = ? AND crm = ? AND status = 'ativo'
    """
    with _conectar() as conn:
        cursor = conn.execute(sql, (codigo, crm))
        conn.commit()
        return cursor.rowcount > 0
