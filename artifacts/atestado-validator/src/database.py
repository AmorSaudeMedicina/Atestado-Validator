"""
Camada de acesso ao banco de dados SQLite.

Guarda os atestados emitidos de forma persistente entre sessões.
Banco criado automaticamente em data/atestados.db na primeira execução.
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

from src.crypto import criptografar, descriptografar, validar_chave_na_subida

# Caminho do banco de dados, configurável para deploys com disco persistente
# (ex.: um Volume do Railway) sem precisar alterar código:
#   - DATABASE_PATH: caminho completo do arquivo .db (tem prioridade).
#   - DATA_DIR: diretório onde o arquivo "atestados.db" deve ficar.
# Sem nenhuma das duas, cai no caminho local de desenvolvimento de sempre
# (baseado na localização deste arquivo, subindo um nível até a raiz do projeto).
_DB_DIR_PADRAO = Path(__file__).resolve().parent.parent / "data"
_DB_DIR = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else _DB_DIR_PADRAO
_DB_PATH = Path(os.environ["DATABASE_PATH"]) if os.environ.get("DATABASE_PATH") else (_DB_DIR / "atestados.db")

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
    # Criptografia em repouso (LGPD/segurança, parte 2): marca se nome_paciente
    # e cid desta linha já estão cifrados (1) ou ainda em texto puro (0).
    # Linhas NOVAS gravadas por salvar_atestado() já entram com cifrado=1;
    # linhas existentes de antes desta migração entram com 0 (valor padrão
    # do ALTER TABLE) e são cifradas uma única vez por
    # `migrar_atestados_para_cifrado()`, chamada em init_db(). Nunca é lido
    # como texto puro por engano: buscar_atestado_por_codigo() e
    # listar_atestados_por_crm() só descriptografam quando cifrado=1.
    ("cifrado", "INTEGER NOT NULL DEFAULT 0"),
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
    # Endurecimento de login (LGPD/segurança para produção):
    # deve_trocar_senha força a troca no próximo login bem-sucedido (usado no
    # seed do admin inicial); tentativas_login_falhas + bloqueado_ate
    # implementam o bloqueio temporário por força bruta (ver src/auth.py).
    ("deve_trocar_senha", "INTEGER NOT NULL DEFAULT 0"),
    ("tentativas_login_falhas", "INTEGER NOT NULL DEFAULT 0"),
    ("bloqueado_ate", "TEXT"),
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

# ---------------------------------------------------------------------------
# Trilha de auditoria (LGPD/segurança, parte 3) — quem fez o quê e quando.
# NUNCA guarda dado sensível de paciente: atestados são referenciados só
# pelo `atestado_codigo` (não sensível — é o mesmo código já público da
# verificação). `detalhe` é só texto operacional curto (ex.: usuário do
# médico afetado por uma ação do admin), nunca nome de paciente nem CID.
# Ver src/audit.py para a política de gravação (nunca derruba a operação
# principal) e de retenção (AUDIT_RETENTION_DAYS).
# ---------------------------------------------------------------------------

_CREATE_EVENTOS_AUDITORIA = """
CREATE TABLE IF NOT EXISTS eventos_auditoria (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    criado_em       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    tipo_evento     TEXT NOT NULL,
    ator_usuario    TEXT,
    ator_perfil     TEXT,
    atestado_codigo TEXT,
    origem          TEXT,
    detalhe         TEXT
)
"""

_CREATE_INDICES_AUDITORIA = (
    "CREATE INDEX IF NOT EXISTS idx_auditoria_criado_em ON eventos_auditoria(criado_em)",
    "CREATE INDEX IF NOT EXISTS idx_auditoria_tipo ON eventos_auditoria(tipo_evento)",
)

# ---------------------------------------------------------------------------
# Documento PDF do atestado (gerado via Canva) — ver src/canva_client.py.
#
# Uma linha por atestado, criada quando a geração do PDF é disparada
# (emissão pelo formulário, API ou MCP) e atualizada quando o job em segundo
# plano termina. `status`: 'gerando' | 'pronto' | 'falhou'. Se a geração
# nunca foi disparada para um atestado (ex.: emitido antes desta
# funcionalidade existir, ou sem CPF informado), simplesmente não há linha —
# o dashboard trata "sem linha" como "documento não disponível", sem erro.
#
# `caminho_arquivo` aponta para um PDF gravado em DATA_DIR, cifrado em
# repouso com a mesma ENCRYPTION_KEY (ver src/crypto.py) — o PDF carrega
# nome e CPF do paciente em claro dentro do próprio documento, então merece
# o mesmo cuidado já dado a nome_paciente/cid no banco.
# ---------------------------------------------------------------------------

_CREATE_DOCUMENTOS_ATESTADO = """
CREATE TABLE IF NOT EXISTS documentos_atestado (
    codigo          TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'gerando' CHECK (status IN ('gerando','pronto','falhou')),
    caminho_arquivo TEXT,
    erro            TEXT,
    tentativas      INTEGER NOT NULL DEFAULT 0,
    criado_em       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    atualizado_em   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
"""

# ---------------------------------------------------------------------------
# OAuth do Canva (servidor como CLIENTE, não como emissor) — ver
# src/canva_client.py. Autorização feita uma única vez pelo admin em
# /admin/canva/conectar; o token fica guardado aqui, cifrado, para uso e
# renovação automática em segundo plano (nunca em variável de ambiente, nem
# em texto puro).
#
# `canva_oauth_token` é uma tabela de UMA linha só (id fixo em 1) — só existe
# uma conexão Canva por vez. `canva_oauth_state` guarda o par
# state/code_verifier (PKCE) durante a ida-e-volta do navegador para o
# Canva; cada linha expira em poucos minutos e é consumida uma única vez.
# ---------------------------------------------------------------------------

_CREATE_CANVA_OAUTH_TOKEN = """
CREATE TABLE IF NOT EXISTS canva_oauth_token (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    access_token_cifrado  TEXT NOT NULL,
    refresh_token_cifrado TEXT NOT NULL,
    expira_em             TEXT NOT NULL,
    conectado_por         TEXT,
    criado_em             TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    atualizado_em         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
"""

_CREATE_CANVA_OAUTH_STATE = """
CREATE TABLE IF NOT EXISTS canva_oauth_state (
    state         TEXT PRIMARY KEY,
    code_verifier TEXT NOT NULL,
    criado_por    TEXT,
    expira_em     TEXT NOT NULL,
    criado_em     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
"""


def _conectar() -> sqlite3.Connection:
    """
    Abre uma conexão nova por chamada (sem conexão compartilhada entre threads).
    WAL mode permite leituras simultâneas sem bloquear escritas.
    timeout=10 evita erros imediatos de 'database is locked' sob carga leve.
    """
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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

    Fail-closed: valida ENCRYPTION_KEY ANTES de qualquer outra coisa — se a
    chave estiver ausente/inválida, o processo falha aqui, na subida, e nunca
    chega a servir uma única requisição sem criptografia configurada.
    """
    validar_chave_na_subida()

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
        conn.execute(_CREATE_EVENTOS_AUDITORIA)
        for sql_indice in _CREATE_INDICES_AUDITORIA:
            conn.execute(sql_indice)
        conn.execute(_CREATE_DOCUMENTOS_ATESTADO)
        conn.execute(_CREATE_CANVA_OAUTH_TOKEN)
        conn.execute(_CREATE_CANVA_OAUTH_STATE)
        conn.commit()

    migrar_atestados_para_cifrado()


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
    deve_trocar_senha: bool = False,
) -> None:
    """
    Cria uma nova conta. `perfil` deve ser 'admin' ou 'medico'.

    `deve_trocar_senha=True` força a troca de senha no próximo login bem
    sucedido dessa conta (usado no seed do administrador inicial).

    Levanta sqlite3.IntegrityError se `usuario` já existir — o chamador deve
    tratar esse caso (ex.: exibir "nome de usuário já em uso").
    """
    sql = """
        INSERT INTO usuarios (usuario, senha_hash, nome, perfil, crm, especialidade, ativo, deve_trocar_senha)
        VALUES (?,?,?,?,?,?,?,?)
    """
    with _conectar() as conn:
        conn.execute(
            sql,
            (usuario, senha_hash, nome, perfil, crm, especialidade, int(ativo), int(deve_trocar_senha)),
        )
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


def redefinir_senha_usuario(usuario_id: int, novo_senha_hash: str, deve_trocar_senha: bool = False) -> bool:
    """
    Substitui o hash de senha de uma conta e define se ela deve trocar a senha
    de novo no próximo login (`deve_trocar_senha=True` — usado só no fluxo de
    troca obrigatória; o padrão `False` cobre tanto o admin redefinindo a
    senha de um médico quanto uma conta trocando a própria senha por vontade
    própria). Retorna True se alterou algum registro.
    """
    sql = "UPDATE usuarios SET senha_hash = ?, deve_trocar_senha = ? WHERE id = ?"
    with _conectar() as conn:
        cursor = conn.execute(sql, (novo_senha_hash, int(deve_trocar_senha), usuario_id))
        conn.commit()
        return cursor.rowcount > 0


def usuario_bloqueado_no_momento(usuario_id: int) -> bool:
    """Verifica se a conta está sob bloqueio temporário por excesso de tentativas de login incorretas."""
    sql = """
        SELECT 1 FROM usuarios
        WHERE id = ? AND bloqueado_ate IS NOT NULL AND bloqueado_ate > datetime('now','localtime')
    """
    with _conectar() as conn:
        row = conn.execute(sql, (usuario_id,)).fetchone()
    return row is not None


def registrar_tentativa_login_falha(usuario_id: int, max_tentativas: int, minutos_bloqueio: int) -> bool:
    """
    Incrementa o contador de tentativas de login falhas de uma conta. Quando o
    contador atinge `max_tentativas`, bloqueia a conta por `minutos_bloqueio`
    minutos a partir de agora. Chamado apenas por `src.auth.autenticar()` a
    cada senha incorreta — nunca recebe a senha em si.

    Retorna True se esta chamada foi a que cruzou o limite e bloqueou a
    conta agora (usado por src.auth para logar um evento de auditoria
    distinto de "login falho" — "conta bloqueada" — só no momento exato em
    que o bloqueio começa, não em cada tentativa repetida durante o bloqueio).
    """
    modificador = f"+{int(minutos_bloqueio)} minutes"
    sql = """
        UPDATE usuarios
        SET tentativas_login_falhas = tentativas_login_falhas + 1,
            bloqueado_ate = CASE
                WHEN tentativas_login_falhas + 1 >= ? THEN datetime('now','localtime', ?)
                ELSE bloqueado_ate
            END
        WHERE id = ?
    """
    with _conectar() as conn:
        conn.execute(sql, (int(max_tentativas), modificador, usuario_id))
        conn.commit()
        row = conn.execute(
            "SELECT tentativas_login_falhas FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
    return bool(row and row["tentativas_login_falhas"] == int(max_tentativas))


def resetar_tentativas_login(usuario_id: int) -> None:
    """Zera o contador de tentativas falhas e remove qualquer bloqueio ativo (chamado após login bem-sucedido)."""
    sql = "UPDATE usuarios SET tentativas_login_falhas = 0, bloqueado_ate = NULL WHERE id = ?"
    with _conectar() as conn:
        conn.execute(sql, (usuario_id,))
        conn.commit()


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


def migrar_atestados_para_cifrado() -> int:
    """
    Criptografa nome_paciente/cid de atestados gravados ANTES da criptografia
    em repouso ter sido introduzida (linhas com cifrado=0), uma única vez.

    Idempotente e seguro de chamar toda subida (é chamada por `init_db()`):
    só processa linhas ainda com cifrado=0 — depois da primeira execução bem
    sucedida vira um SELECT vazio, praticamente instantâneo. Nunca apaga nem
    recria linha nenhuma, só faz UPDATE campo a campo; nenhum atestado é
    perdido. Retorna quantas linhas foram migradas nesta chamada.
    """
    with _conectar() as conn:
        linhas = conn.execute(
            "SELECT id, nome_paciente, cid FROM atestados WHERE cifrado = 0"
        ).fetchall()
        for linha in linhas:
            conn.execute(
                "UPDATE atestados SET nome_paciente = ?, cid = ?, cifrado = 1 WHERE id = ?",
                (criptografar(linha["nome_paciente"]), criptografar(linha["cid"]), linha["id"]),
            )
        conn.commit()
    return len(linhas)


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
    """
    Persiste um novo atestado no banco. `nome_paciente` e `cid` são gravados
    criptografados (ver src/crypto.py) — nunca em texto puro.
    """
    sql = """
        INSERT INTO atestados
            (codigo, nome_medico, crm, nome_paciente, cid,
             data_emissao, data_inicio, data_fim, dias_afastamento, cifrado)
        VALUES (?,?,?,?,?,?,?,?,?,1)
    """
    with _conectar() as conn:
        conn.execute(
            sql,
            (codigo, nome_medico, crm, criptografar(nome_paciente), criptografar(cid),
             data_emissao, data_inicio, data_fim, dias_afastamento),
        )
        conn.commit()


def _descriptografar_atestado(linha: dict) -> dict:
    """
    Descriptografa nome_paciente/cid de uma linha da tabela `atestados` antes
    de devolvê-la ao chamador — transparente para quem consome (dashboard,
    API, MCP, página de verificação). Só descriptografa se `cifrado=1`; uma
    linha ainda não migrada (cifrado=0, texto puro) é devolvida como está —
    isso nunca deveria acontecer em uso normal (init_db() já migra tudo na
    subida), mas evita quebrar/corromper dado se acontecer numa janela rara.
    """
    atestado = dict(linha)
    if atestado.get("cifrado"):
        atestado["nome_paciente"] = descriptografar(atestado["nome_paciente"])
        atestado["cid"] = descriptografar(atestado["cid"])
    return atestado


def buscar_atestado_por_codigo(codigo: str) -> Optional[dict]:
    """Retorna os dados do atestado (nome_paciente/cid já descriptografados) ou None se não encontrado."""
    sql = "SELECT * FROM atestados WHERE codigo = ?"
    with _conectar() as conn:
        row = conn.execute(sql, (codigo,)).fetchone()
    return _descriptografar_atestado(row) if row else None


def listar_atestados_por_crm(crm: str) -> list[dict]:
    """Retorna todos os atestados emitidos por um médico (nome_paciente/cid já descriptografados), mais recentes primeiro."""
    sql = "SELECT * FROM atestados WHERE crm = ? ORDER BY id DESC"
    with _conectar() as conn:
        rows = conn.execute(sql, (crm,)).fetchall()
    return [_descriptografar_atestado(r) for r in rows]


def anonimizar_atestado(codigo: str) -> bool:
    """
    Remove os dados sensíveis (nome_paciente, cid) de um atestado, mantendo os
    campos operacionais (código, datas, período, status anterior é
    substituído por 'anonimizado'). Usada tanto pela ferramenta manual do
    admin (pedido de exclusão do titular, LGPD) quanto pela retenção
    automática opt-in — ver src/retencao.py, que decide QUANDO chamar isto e
    grava o evento de auditoria (aqui é só o UPDATE).

    Idempotente: não faz nada se o atestado já estiver anonimizado. Retorna
    True se anonimizou agora, False se não encontrado ou já anonimizado.

    Usa string vazia (não NULL) em nome_paciente/cid — as colunas têm
    restrição NOT NULL desde a criação da tabela; string vazia é "falsy" em
    Python, então o restante do app (dashboard, verificação pública) já
    trata isso como "sem dado", sem precisar de nenhum tratamento especial.
    """
    sql = """
        UPDATE atestados
        SET nome_paciente = '', cid = '', cifrado = 0, status = 'anonimizado'
        WHERE codigo = ? AND status != 'anonimizado'
    """
    with _conectar() as conn:
        cursor = conn.execute(sql, (codigo,))
        conn.commit()
        return cursor.rowcount > 0


def excluir_atestado_definitivamente(codigo: str) -> bool:
    """
    Remove PERMANENTEMENTE um atestado do banco — sem recuperação possível.
    Usada só pela ferramenta manual do admin (pedido de exclusão do
    titular). Retorna True se algum registro foi apagado.
    """
    with _conectar() as conn:
        cursor = conn.execute("DELETE FROM atestados WHERE codigo = ?", (codigo,))
        conn.commit()
        return cursor.rowcount > 0


def listar_codigos_atestados_para_retencao(dias_retencao: int) -> list[str]:
    """
    Retorna os códigos de atestados emitidos há mais de `dias_retencao` dias
    (com base em `data_emissao`) e ainda não anonimizados — usada só pela
    retenção automática opt-in (src/retencao.py) para decidir o que
    anonimizar. Nunca inclui atestados já anonimizados (evita reprocessar).
    """
    modificador = f"-{int(dias_retencao)} days"
    sql = """
        SELECT codigo FROM atestados
        WHERE status != 'anonimizado'
              AND data_emissao IS NOT NULL
              AND date(data_emissao) < date('now', 'localtime', ?)
    """
    with _conectar() as conn:
        rows = conn.execute(sql, (modificador,)).fetchall()
    return [r["codigo"] for r in rows]


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


# ---------------------------------------------------------------------------
# Trilha de auditoria — CRUD puro (a política de "nunca derruba a operação
# principal" e a leitura de AUDIT_RETENTION_DAYS ficam em src/audit.py, que
# chama estas funções; mantê-las aqui, sem regras de negócio, evita import
# circular entre database.py e audit.py).
# ---------------------------------------------------------------------------

def inserir_evento_auditoria(
    tipo_evento: str,
    ator_usuario: Optional[str],
    ator_perfil: Optional[str],
    atestado_codigo: Optional[str],
    origem: Optional[str],
    detalhe: Optional[str],
) -> None:
    """Grava um evento de auditoria. Levanta exceção normalmente em caso de erro — quem chama (src.audit) trata."""
    sql = """
        INSERT INTO eventos_auditoria
            (tipo_evento, ator_usuario, ator_perfil, atestado_codigo, origem, detalhe)
        VALUES (?,?,?,?,?,?)
    """
    with _conectar() as conn:
        conn.execute(sql, (tipo_evento, ator_usuario, ator_perfil, atestado_codigo, origem, detalhe))
        conn.commit()


def listar_eventos_auditoria(
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    tipo_evento: Optional[str] = None,
    pagina: int = 1,
    por_pagina: int = 25,
) -> tuple[list[dict], int]:
    """
    Retorna (eventos da página, total de eventos que batem com o filtro),
    mais recentes primeiro. `data_inicio`/`data_fim` no formato 'AAAA-MM-DD'
    (inclusive nas duas pontas); `tipo_evento` filtra por um tipo exato.
    """
    condicoes = []
    params: list = []
    if data_inicio:
        condicoes.append("date(criado_em) >= date(?)")
        params.append(data_inicio)
    if data_fim:
        condicoes.append("date(criado_em) <= date(?)")
        params.append(data_fim)
    if tipo_evento:
        condicoes.append("tipo_evento = ?")
        params.append(tipo_evento)
    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""

    pagina = max(1, int(pagina))
    por_pagina = max(1, int(por_pagina))
    offset = (pagina - 1) * por_pagina

    with _conectar() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM eventos_auditoria {where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM eventos_auditoria {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [por_pagina, offset],
        ).fetchall()
    return [dict(r) for r in rows], int(total)


def limpar_eventos_auditoria_antigos(dias_retencao: int) -> int:
    """Remove eventos de auditoria com mais de `dias_retencao` dias. Retorna quantos foram removidos."""
    modificador = f"-{int(dias_retencao)} days"
    with _conectar() as conn:
        cursor = conn.execute(
            "DELETE FROM eventos_auditoria WHERE criado_em < datetime('now','localtime', ?)",
            (modificador,),
        )
        conn.commit()
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Documento PDF do atestado (Canva) — ver src/canva_client.py.
# ---------------------------------------------------------------------------

def iniciar_geracao_documento(codigo: str) -> None:
    """
    Cria (ou reinicia, se já existir) o registro de geração do PDF de um
    atestado como 'gerando', incrementando `tentativas`. Chamada tanto na
    emissão quanto no botão "Tentar novamente" do dashboard — em ambos os
    casos o job em segundo plano parte do zero.
    """
    sql = """
        INSERT INTO documentos_atestado (codigo, status, tentativas)
        VALUES (?, 'gerando', 1)
        ON CONFLICT(codigo) DO UPDATE SET
            status = 'gerando',
            erro = NULL,
            tentativas = tentativas + 1,
            atualizado_em = datetime('now','localtime')
    """
    with _conectar() as conn:
        conn.execute(sql, (codigo,))
        conn.commit()


def marcar_documento_pronto(codigo: str, caminho_arquivo: str) -> None:
    """Marca o documento como pronto, com o caminho do PDF cifrado gravado em DATA_DIR."""
    sql = """
        UPDATE documentos_atestado
        SET status = 'pronto', caminho_arquivo = ?, erro = NULL, atualizado_em = datetime('now','localtime')
        WHERE codigo = ?
    """
    with _conectar() as conn:
        conn.execute(sql, (caminho_arquivo, codigo))
        conn.commit()


def marcar_documento_falhou(codigo: str, erro: str) -> None:
    """Marca a geração do documento como falha, com uma mensagem curta (nunca dado sensível) para exibir no dashboard."""
    sql = """
        UPDATE documentos_atestado
        SET status = 'falhou', erro = ?, atualizado_em = datetime('now','localtime')
        WHERE codigo = ?
    """
    with _conectar() as conn:
        conn.execute(sql, (erro, codigo))
        conn.commit()


def buscar_documento(codigo: str) -> Optional[dict]:
    """Retorna o registro de geração do PDF de um atestado, ou None se nunca foi disparada para esse código."""
    with _conectar() as conn:
        row = conn.execute("SELECT * FROM documentos_atestado WHERE codigo = ?", (codigo,)).fetchone()
    return dict(row) if row else None


def remover_registro_documento(codigo: str) -> Optional[str]:
    """
    Remove o registro de documento de um atestado (chamada quando o atestado é
    anonimizado ou excluído — ver src/retencao.py). Retorna o `caminho_arquivo`
    que estava associado (para o chamador apagar o PDF do disco), ou None se
    não havia documento gerado.
    """
    with _conectar() as conn:
        row = conn.execute(
            "SELECT caminho_arquivo FROM documentos_atestado WHERE codigo = ?", (codigo,)
        ).fetchone()
        conn.execute("DELETE FROM documentos_atestado WHERE codigo = ?", (codigo,))
        conn.commit()
    return row["caminho_arquivo"] if row else None


# ---------------------------------------------------------------------------
# OAuth do Canva (servidor como cliente) — ver src/canva_client.py.
# ---------------------------------------------------------------------------

def salvar_canva_oauth_token(
    access_token_cifrado: str,
    refresh_token_cifrado: str,
    expira_em_iso: str,
    conectado_por: Optional[str] = None,
) -> None:
    """
    Grava (substituindo qualquer conexão anterior) o token OAuth do Canva —
    tabela de uma linha só (id=1). Chamada tanto na autorização inicial
    quanto a cada renovação automática (o refresh token do Canva é de uso
    único: cada renovação grava um refresh token novo).
    """
    sql = """
        INSERT INTO canva_oauth_token (id, access_token_cifrado, refresh_token_cifrado, expira_em, conectado_por)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            access_token_cifrado = excluded.access_token_cifrado,
            refresh_token_cifrado = excluded.refresh_token_cifrado,
            expira_em = excluded.expira_em,
            conectado_por = COALESCE(excluded.conectado_por, canva_oauth_token.conectado_por),
            atualizado_em = datetime('now','localtime')
    """
    with _conectar() as conn:
        conn.execute(sql, (access_token_cifrado, refresh_token_cifrado, expira_em_iso, conectado_por))
        conn.commit()


def buscar_canva_oauth_token() -> Optional[dict]:
    """Retorna a conexão Canva atual (tokens ainda cifrados — decifrar é responsabilidade do chamador), ou None se nunca autorizada."""
    with _conectar() as conn:
        row = conn.execute("SELECT * FROM canva_oauth_token WHERE id = 1").fetchone()
    return dict(row) if row else None


def remover_canva_oauth_token() -> None:
    """Remove a conexão Canva atual — usada quando o admin desconecta explicitamente (ex.: antes de trocar de conta)."""
    with _conectar() as conn:
        conn.execute("DELETE FROM canva_oauth_token WHERE id = 1")
        conn.commit()


def criar_canva_oauth_state(state: str, code_verifier: str, criado_por: Optional[str] = None) -> None:
    """Grava o par state/code_verifier (PKCE) durante a ida-e-volta do navegador para o Canva, válido por 10 minutos."""
    sql = """
        INSERT INTO canva_oauth_state (state, code_verifier, criado_por, expira_em)
        VALUES (?, ?, ?, datetime('now','localtime','+10 minutes'))
    """
    with _conectar() as conn:
        conn.execute(sql, (state, code_verifier, criado_por))
        conn.commit()


def consumir_canva_oauth_state(state: str) -> Optional[dict]:
    """Busca e remove (uso único) um state ainda válido. Retorna o registro ou None se inexistente/expirado/já usado."""
    with _conectar() as conn:
        row = conn.execute(
            "SELECT * FROM canva_oauth_state WHERE state = ? AND expira_em > datetime('now','localtime')",
            (state,),
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM canva_oauth_state WHERE state = ?", (state,))
        conn.commit()
        return dict(row)
