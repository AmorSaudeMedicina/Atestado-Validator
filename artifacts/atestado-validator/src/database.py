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

_CREATE_USUARIOS = """
CREATE TABLE IF NOT EXISTS usuarios (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario             TEXT    UNIQUE NOT NULL,
    senha_hash          TEXT    NOT NULL,
    nome                TEXT    NOT NULL,
    perfil              TEXT    NOT NULL CHECK (perfil IN ('admin','medico')),
    crm                 TEXT,
    especialidade       TEXT,
    ativo               INTEGER NOT NULL DEFAULT 1,
    criado_em           TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    -- Campos reservados para funcionalidades futuras (não usados ainda):
    -- verificação de CRM junto ao CFM, confirmação de e-mail e recuperação
    -- de senha por e-mail. Deixados aqui apenas para não exigir migração
    -- de schema quando essas fases forem implementadas.
    email               TEXT,
    email_verificado    INTEGER NOT NULL DEFAULT 0,
    crm_verificado_cfm  INTEGER NOT NULL DEFAULT 0,
    reset_senha_token   TEXT,
    reset_senha_expira  TEXT
)
"""

# Colunas de token de API adicionadas depois da criação inicial da tabela
# usuarios. Nunca guardamos o token em texto puro — apenas um hash (SHA-256
# é suficiente aqui porque o token é aleatório de alta entropia, ao
# contrário de uma senha escolhida por humano) e os 4 últimos caracteres,
# só para exibição/identificação na interface.
_MIGRACOES_COLUNAS_USUARIOS = [
    ("api_token_hash", "TEXT"),
    ("api_token_ultimos4", "TEXT"),
    ("api_token_criado_em", "TEXT"),
]

# ---------------------------------------------------------------------------
# OAuth 2.0 (Dynamic Client Registration + Authorization Code + PKCE) — usado
# apenas pelo conector MCP, para que a Claude descubra e autentique via o
# fluxo de autorização previsto pelo próprio protocolo MCP, em vez do token
# de API embutido na URL. Nenhuma senha é guardada aqui: apenas hash do
# access token, igual ao padrão já usado para o token de API (api_tokens.py).
# ---------------------------------------------------------------------------

_CREATE_OAUTH_CLIENTS = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_name   TEXT,
    redirect_uris TEXT NOT NULL,
    criado_em     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
"""

_CREATE_OAUTH_AUTH_CODES = """
CREATE TABLE IF NOT EXISTS oauth_auth_codes (
    codigo               TEXT PRIMARY KEY,
    client_id            TEXT NOT NULL,
    redirect_uri          TEXT NOT NULL,
    code_challenge        TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL,
    usuario_id            INTEGER NOT NULL,
    usado                 INTEGER NOT NULL DEFAULT 0,
    expira_em             TEXT NOT NULL,
    criado_em             TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
"""

_CREATE_OAUTH_ACCESS_TOKENS = """
CREATE TABLE IF NOT EXISTS oauth_access_tokens (
    token_hash TEXT PRIMARY KEY,
    usuario_id INTEGER NOT NULL,
    client_id  TEXT NOT NULL,
    expira_em  TEXT NOT NULL,
    criado_em  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
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
        conn.execute(_CREATE_USUARIOS)
        colunas_usuarios_existentes = {
            linha["name"] for linha in conn.execute("PRAGMA table_info(usuarios)")
        }
        for nome_coluna, definicao_sql in _MIGRACOES_COLUNAS_USUARIOS:
            if nome_coluna not in colunas_usuarios_existentes:
                conn.execute(f"ALTER TABLE usuarios ADD COLUMN {nome_coluna} {definicao_sql}")
        conn.execute(_CREATE_OAUTH_CLIENTS)
        conn.execute(_CREATE_OAUTH_AUTH_CODES)
        conn.execute(_CREATE_OAUTH_ACCESS_TOKENS)
        conn.commit()


# ---------------------------------------------------------------------------
# Usuários (administradores e médicos) — autenticação e gestão de contas
# ---------------------------------------------------------------------------
#
# Esta camada nunca lida com senha em texto puro nem calcula hash — ela apenas
# grava/lê o hash que já vem pronto de src/auth.py. Isso mantém a política de
# hashing centralizada num único lugar (src/auth.py).

def contar_usuarios() -> int:
    """Total de contas cadastradas — usado para decidir se o seed inicial já rodou."""
    with _conectar() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()
    return int(row["n"])


def criar_usuario(
    usuario: str,
    senha_hash: str,
    nome: str,
    perfil: str,
    crm: Optional[str] = None,
    especialidade: Optional[str] = None,
    ativo: bool = True,
) -> None:
    """
    Cria uma nova conta. `perfil` deve ser 'admin' ou 'medico'.

    Levanta sqlite3.IntegrityError se `usuario` já existir — o chamador deve
    tratar esse caso (ex.: exibir "nome de usuário já em uso").
    """
    sql = """
        INSERT INTO usuarios (usuario, senha_hash, nome, perfil, crm, especialidade, ativo)
        VALUES (?,?,?,?,?,?,?)
    """
    with _conectar() as conn:
        conn.execute(sql, (usuario, senha_hash, nome, perfil, crm, especialidade, int(ativo)))
        conn.commit()


def buscar_usuario_por_login(usuario: str) -> Optional[dict]:
    """Retorna a conta pelo nome de usuário (para checagem de login), ou None."""
    sql = "SELECT * FROM usuarios WHERE usuario = ?"
    with _conectar() as conn:
        row = conn.execute(sql, (usuario,)).fetchone()
    return dict(row) if row else None


def buscar_usuario_por_id(usuario_id: int) -> Optional[dict]:
    """Retorna a conta pelo id, ou None."""
    sql = "SELECT * FROM usuarios WHERE id = ?"
    with _conectar() as conn:
        row = conn.execute(sql, (usuario_id,)).fetchone()
    return dict(row) if row else None


def listar_medicos() -> list[dict]:
    """Retorna todas as contas de médico cadastradas, ordenadas por nome."""
    sql = "SELECT * FROM usuarios WHERE perfil = 'medico' ORDER BY nome"
    with _conectar() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def definir_status_usuario(usuario_id: int, ativo: bool) -> bool:
    """Ativa ou desativa uma conta. Retorna True se algum registro foi alterado."""
    sql = "UPDATE usuarios SET ativo = ? WHERE id = ?"
    with _conectar() as conn:
        cursor = conn.execute(sql, (int(ativo), usuario_id))
        conn.commit()
        return cursor.rowcount > 0


def redefinir_senha_usuario(usuario_id: int, novo_senha_hash: str) -> bool:
    """Substitui o hash de senha de uma conta. Retorna True se alterou algum registro."""
    sql = "UPDATE usuarios SET senha_hash = ? WHERE id = ?"
    with _conectar() as conn:
        cursor = conn.execute(sql, (novo_senha_hash, usuario_id))
        conn.commit()
        return cursor.rowcount > 0


def salvar_token_api(usuario_id: int, token_hash: str, ultimos4: str) -> bool:
    """
    Grava o hash do novo token de API de um médico (gerado em src/api_tokens.py),
    substituindo qualquer token anterior — isso invalida automaticamente o
    token antigo (regeneração = revogação implícita do anterior).
    """
    sql = """
        UPDATE usuarios
        SET api_token_hash = ?, api_token_ultimos4 = ?, api_token_criado_em = datetime('now','localtime')
        WHERE id = ?
    """
    with _conectar() as conn:
        cursor = conn.execute(sql, (token_hash, ultimos4, usuario_id))
        conn.commit()
        return cursor.rowcount > 0


def revogar_token_api(usuario_id: int) -> bool:
    """Remove o token de API de um médico (chamadas com o token antigo passam a ser recusadas)."""
    sql = """
        UPDATE usuarios
        SET api_token_hash = NULL, api_token_ultimos4 = NULL, api_token_criado_em = NULL
        WHERE id = ?
    """
    with _conectar() as conn:
        cursor = conn.execute(sql, (usuario_id,))
        conn.commit()
        return cursor.rowcount > 0


def buscar_medico_por_token_hash(token_hash: str) -> Optional[dict]:
    """
    Resolve um token de API (já convertido em hash pelo chamador) para a conta
    de médico dona dele. Só retorna a conta se ela for de perfil 'medico' E
    estiver ativa — isso garante que um token de médico desativado nunca
    autentica uma chamada, mesmo que o hash ainda esteja salvo no banco.
    """
    sql = """
        SELECT * FROM usuarios
        WHERE api_token_hash = ? AND perfil = 'medico' AND ativo = 1
    """
    with _conectar() as conn:
        row = conn.execute(sql, (token_hash,)).fetchone()
    return dict(row) if row else None


def criar_oauth_client(client_id: str, client_name: str, redirect_uris_json: str) -> None:
    """Registra um novo cliente OAuth (Dynamic Client Registration, ex.: a própria Claude)."""
    sql = "INSERT INTO oauth_clients (client_id, client_name, redirect_uris) VALUES (?,?,?)"
    with _conectar() as conn:
        conn.execute(sql, (client_id, client_name, redirect_uris_json))
        conn.commit()


def buscar_oauth_client(client_id: str) -> Optional[dict]:
    """Retorna o cliente OAuth registrado, ou None."""
    sql = "SELECT * FROM oauth_clients WHERE client_id = ?"
    with _conectar() as conn:
        row = conn.execute(sql, (client_id,)).fetchone()
    return dict(row) if row else None


def criar_oauth_auth_code(
    codigo: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    usuario_id: int,
) -> None:
    """Grava um código de autorização (uso único, válido por 5 minutos) após o login do médico."""
    sql = """
        INSERT INTO oauth_auth_codes
            (codigo, client_id, redirect_uri, code_challenge, code_challenge_method, usuario_id, expira_em)
        VALUES (?,?,?,?,?,?, datetime('now','localtime','+5 minutes'))
    """
    with _conectar() as conn:
        conn.execute(sql, (codigo, client_id, redirect_uri, code_challenge, code_challenge_method, usuario_id))
        conn.commit()


def consumir_oauth_auth_code(codigo: str) -> Optional[dict]:
    """
    Busca e consome (marca como usado) um código de autorização ainda válido e não usado.

    Retorna os dados do código (para validação de client_id/redirect_uri/PKCE pelo
    chamador) ou None se não existir, já tiver sido usado, ou tiver expirado. O
    UPDATE com `WHERE usado = 0` garante que o código nunca seja consumido duas
    vezes, mesmo sob chamadas concorrentes.
    """
    with _conectar() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_auth_codes WHERE codigo = ? AND usado = 0 AND expira_em > datetime('now','localtime')",
            (codigo,),
        ).fetchone()
        if not row:
            return None
        cursor = conn.execute(
            "UPDATE oauth_auth_codes SET usado = 1 WHERE codigo = ? AND usado = 0", (codigo,)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return None
        return dict(row)


def criar_oauth_access_token(token_hash: str, usuario_id: int, client_id: str, dias_validade: int = 180) -> None:
    """Grava o hash de um novo access token do conector MCP, válido por `dias_validade` dias."""
    sql = f"""
        INSERT INTO oauth_access_tokens (token_hash, usuario_id, client_id, expira_em)
        VALUES (?,?,?, datetime('now','localtime','+{int(dias_validade)} days'))
    """
    with _conectar() as conn:
        conn.execute(sql, (token_hash, usuario_id, client_id))
        conn.commit()


def buscar_medico_por_oauth_token_hash(token_hash: str) -> Optional[dict]:
    """
    Resolve um access token OAuth do conector MCP (já em hash) para a conta de
    médico dona dele — só retorna a conta se o token existir, não tiver
    expirado, e a conta for de médico ativo (mesmas garantias do token de API
    tradicional, ver `buscar_medico_por_token_hash`).
    """
    sql = """
        SELECT u.* FROM oauth_access_tokens t
        JOIN usuarios u ON u.id = t.usuario_id
        WHERE t.token_hash = ? AND t.expira_em > datetime('now','localtime')
              AND u.perfil = 'medico' AND u.ativo = 1
    """
    with _conectar() as conn:
        row = conn.execute(sql, (token_hash,)).fetchone()
    return dict(row) if row else None


def contar_oauth_access_tokens_ativos(usuario_id: int) -> int:
    """Quantos access tokens do conector MCP ainda válidos (não expirados) esse médico tem."""
    sql = """
        SELECT COUNT(*) AS n FROM oauth_access_tokens
        WHERE usuario_id = ? AND expira_em > datetime('now','localtime')
    """
    with _conectar() as conn:
        row = conn.execute(sql, (usuario_id,)).fetchone()
    return int(row["n"])


def revogar_oauth_access_tokens(usuario_id: int) -> int:
    """Remove todos os access tokens do conector MCP desse médico. Retorna quantos foram removidos."""
    with _conectar() as conn:
        cursor = conn.execute("DELETE FROM oauth_access_tokens WHERE usuario_id = ?", (usuario_id,))
        conn.commit()
        return cursor.rowcount


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
