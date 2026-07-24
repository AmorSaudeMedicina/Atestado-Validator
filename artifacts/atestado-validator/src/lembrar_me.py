"""
lembrar_me.py — Sessão de longa duração ("Lembrar de mim neste dispositivo").

Um cookie httpOnly de verdade só pode ser definido por uma resposta HTTP
real (cabeçalho `Set-Cookie`) — o script do Streamlit roda por WebSocket
depois da carga inicial da página e não tem como emitir esse cabeçalho
diretamente durante um rerun. Por isso o fluxo é em duas etapas (ver também
o comentário junto às tabelas em src/database.py):

1. O formulário de login (dentro do Streamlit, `tela_login()` em app.py)
   autentica normalmente. Se "lembrar de mim" estiver marcado, em vez de só
   guardar a sessão, gera um token de HANDOFF de uso único e validade
   curtíssima (60s) e redireciona o navegador (instantâneo, via meta
   refresh) para `/auth/lembrar-me?token=...`.
2. Essa rota HTTP dedicada (`src/auth_routes.py`, fora do ciclo do
   Streamlit) troca o handoff pelo token de "lembrar de mim" de verdade (30
   dias) e É QUEM de fato define o cookie httpOnly, antes de redirecionar
   de volta para a página principal.

Depois disso, a cada carregamento da página, `app.py` lê o cookie via
`st.context.cookies` (API só de leitura, mas suficiente aqui) e, se o token
bater com um registro válido no banco, loga automaticamente.

Nunca guardamos o valor bruto de nenhum token — só o hash (SHA-256, mesmo
padrão já usado para token de API em src/api_tokens.py: os dois são
strings aleatórias de alta entropia geradas por `secrets.token_urlsafe`,
então um hash rápido já é seguro contra força bruta, sem precisar do custo
computacional do bcrypt usado para senha escolhida por humano).
"""

from __future__ import annotations

import secrets
from typing import Optional

from src.api_tokens import hash_token
from src.database import (
    buscar_usuario_por_lembrar_me_token_hash,
    consumir_lembrar_me_handoff,
    criar_lembrar_me_handoff,
    criar_lembrar_me_token,
    revogar_lembrar_me_token,
    revogar_lembrar_me_tokens_usuario,
)

NOME_COOKIE = "lembrar_me"
DIAS_VALIDADE_TOKEN = 30
MAX_AGE_COOKIE_SEGUNDOS = DIAS_VALIDADE_TOKEN * 24 * 60 * 60


def gerar_handoff(usuario_id: int) -> str:
    """
    Gera um token de handoff de uso único (60s) para um login que marcou
    "lembrar de mim". Devolve o valor BRUTO — quem chama monta o
    redirecionamento para /auth/lembrar-me com esse valor na query string.
    """
    token_bruto = secrets.token_urlsafe(32)
    criar_lembrar_me_handoff(hash_token(token_bruto), usuario_id)
    return token_bruto


def trocar_handoff_por_cookie(token_handoff: str) -> Optional[str]:
    """
    Consome o token de handoff (uso único) e, se ainda válido, gera o token
    de "lembrar de mim" de longa duração de verdade, gravando só o hash no
    banco. Devolve o valor BRUTO a gravar no cookie, ou None se o handoff
    for inválido/expirado/já usado (nesse caso, quem chama simplesmente não
    define cookie nenhum — o login em si já aconteceu normalmente).
    """
    usuario_id = consumir_lembrar_me_handoff(hash_token(token_handoff))
    if usuario_id is None:
        return None
    token_bruto = secrets.token_urlsafe(32)
    criar_lembrar_me_token(hash_token(token_bruto), usuario_id, dias_validade=DIAS_VALIDADE_TOKEN)
    return token_bruto


def autenticar_por_cookie(valor_cookie: Optional[str]) -> Optional[dict]:
    """Resolve o valor do cookie 'lembrar_me' para a conta dona dele, ou None se ausente/inválido/expirado/inativo."""
    if not valor_cookie:
        return None
    return buscar_usuario_por_lembrar_me_token_hash(hash_token(valor_cookie))


def revogar_cookie_atual(valor_cookie: Optional[str]) -> None:
    """Revoga (no banco) o token correspondente ao cookie atual, se houver — usado ao clicar 'Sair' (só este navegador)."""
    if valor_cookie:
        revogar_lembrar_me_token(hash_token(valor_cookie))


def revogar_todos_do_usuario(usuario_id: int) -> None:
    """Revoga TODOS os tokens de 'lembrar de mim' de um usuário — usado ao trocar senha (cookie roubado não sobrevive)."""
    revogar_lembrar_me_tokens_usuario(usuario_id)
