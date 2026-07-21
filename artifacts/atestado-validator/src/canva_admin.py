"""
canva_admin.py — Rotas HTTP para um administrador autorizar o servidor no
Canva (OAuth 2.0 + PKCE), uma única vez.

Espelha o padrão de tela própria de login já usado em
`src/oauth_server.py` (`/oauth/authorize`) — não depende da sessão do
Streamlit porque estas são rotas HTTP puras (Starlette), registradas em
server.py. A diferença é o SENTIDO do fluxo: em oauth_server.py, ESTE
servidor é quem EMITE tokens (para o conector MCP); aqui, este servidor é
quem se autentica PERANTE o Canva, como cliente.

Ao trocar de conta do Canva (ex.: da conta de TESTE para a de produção),
basta acessar /admin/canva/conectar de novo, já logado na nova conta no
navegador — a autorização substitui automaticamente o token anterior
(ver `src.database.salvar_canva_oauth_token`, que sempre sobrescreve a
única linha existente).
"""

from __future__ import annotations

import html
import os
import secrets

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from src.audit import EVENTO_CANVA_CONECTADO, ORIGEM_PAINEL_ADMIN, registrar_evento
from src.auth import autenticar
from src.canva_client import (
    CanvaNaoConfigurado,
    ErroCanva,
    configurado,
    gerar_par_pkce,
    trocar_codigo_por_token,
    url_autorizacao,
)
from src.database import consumir_canva_oauth_state, criar_canva_oauth_state
from src.urls import url_base

_ESTILO_PAGINA = """
  body { font-family: system-ui, sans-serif; background:#eef6f8; display:flex; justify-content:center;
         padding-top:8vh; color:#0f3d47; }
  .cartao { background:#fff; border-radius:16px; padding:32px; max-width:420px; width:100%;
            box-shadow:0 4px 24px rgba(0,0,0,.08); }
  h1 { font-size:1.25rem; margin-bottom:4px; }
  p.aviso { font-size:.92rem; color:#456; margin-top:0; }
  label { display:block; margin-top:14px; font-size:.9rem; font-weight:600; }
  input[type=text], input[type=password] { width:100%; padding:10px; margin-top:4px; border-radius:8px;
            border:1px solid #cdd; font-size:1rem; box-sizing:border-box; }
  button { margin-top:20px; width:100%; padding:11px; border-radius:8px; border:none; background:#e2434d;
            color:#fff; font-size:1rem; font-weight:600; cursor:pointer; }
"""


def _redirect_uri(request: Request) -> str:
    return f"{url_base(request).rstrip('/')}/admin/canva/callback"


def _pagina_login(*, erro: str | None = None) -> HTMLResponse:
    aviso_erro = f'<p style="color:#c0392b;font-weight:600">{html.escape(erro)}</p>' if erro else ""
    pagina = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Conectar Canva — AmorSaúde</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_ESTILO_PAGINA}</style>
</head>
<body>
  <div class="cartao">
    <h1>Conectar o Canva</h1>
    <p class="aviso">Faça login com sua conta de <strong>administrador</strong> do AmorSaúde para autorizar
       este servidor a gerar documentos no Canva (conta atualmente conectada, se houver, será substituída).</p>
    {aviso_erro}
    <form method="post">
      <label>Usuário</label>
      <input type="text" name="usuario" autocomplete="username" required>
      <label>Senha</label>
      <input type="password" name="senha" autocomplete="current-password" required>
      <button type="submit">Entrar e autorizar</button>
    </form>
  </div>
</body>
</html>"""
    return HTMLResponse(pagina)


def _pagina_resultado(titulo: str, mensagem: str, *, sucesso: bool) -> HTMLResponse:
    cor = "#1f7a4d" if sucesso else "#c0392b"
    pagina = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>{html.escape(titulo)} — AmorSaúde</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_ESTILO_PAGINA}
  h1 {{ color:{cor}; }}
  .cartao {{ text-align:center; }}
</style>
</head>
<body><div class="cartao"><h1>{html.escape(titulo)}</h1><p class="aviso">{html.escape(mensagem)}</p></div></body>
</html>"""
    return HTMLResponse(pagina)


async def conectar_canva(request: Request) -> Response:
    """
    GET/POST /admin/canva/conectar — tela de login do admin + início do
    fluxo de autorização OAuth com o Canva.

    GET: mostra o formulário de login.
    POST: valida usuário/senha (mesma função do login normal do app,
    `src.auth.autenticar`) e, se for uma conta de administrador ativa,
    gera o par PKCE, guarda o state/code_verifier (uso único, 10 minutos)
    e redireciona o navegador para a tela de autorização do Canva.
    """
    if not configurado():
        return _pagina_resultado(
            "Canva não configurado",
            "As variáveis de ambiente CANVA_CLIENT_ID e CANVA_CLIENT_SECRET ainda não foram "
            "definidas no servidor. Configure-as (ver CLAUDE.md) e tente de novo.",
            sucesso=False,
        )

    if request.method == "GET":
        return _pagina_login()

    form = await request.form()
    usuario = str(form.get("usuario") or "").strip()
    senha = str(form.get("senha") or "")
    conta = autenticar(usuario, senha)
    if not conta:
        return _pagina_login(erro="Usuário ou senha incorretos.")
    if conta["perfil"] != "admin":
        return _pagina_login(erro="Apenas contas de administrador podem conectar o Canva.")

    code_verifier, code_challenge = gerar_par_pkce()
    state = secrets.token_urlsafe(24)
    criar_canva_oauth_state(state, code_verifier, criado_por=conta["usuario"])

    url = url_autorizacao(_redirect_uri(request), state, code_challenge)
    return RedirectResponse(url=url, status_code=302)


async def callback_canva(request: Request) -> Response:
    """GET /admin/canva/callback — o Canva redireciona para cá depois do admin autorizar (ou recusar)."""
    erro_canva = request.query_params.get("error")
    if erro_canva:
        return _pagina_resultado(
            "Autorização recusada", f"O Canva recusou a autorização: {erro_canva}.", sucesso=False
        )

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return _pagina_resultado(
            "Requisição inválida", "Faltam parâmetros 'code'/'state' no retorno do Canva.", sucesso=False
        )

    registro_state = consumir_canva_oauth_state(state)
    if not registro_state:
        return _pagina_resultado(
            "Sessão de autorização expirada",
            "O link de autorização expirou (10 minutos) ou já foi usado. "
            "Volte em /admin/canva/conectar e tente de novo.",
            sucesso=False,
        )

    ator = registro_state.get("criado_por") or "admin"
    try:
        trocar_codigo_por_token(
            code=code,
            code_verifier=registro_state["code_verifier"],
            redirect_uri=_redirect_uri(request),
            conectado_por=ator,
        )
    except (CanvaNaoConfigurado, ErroCanva) as exc:
        return _pagina_resultado("Falha ao conectar", str(exc), sucesso=False)

    registrar_evento(EVENTO_CANVA_CONECTADO, ator_usuario=ator, ator_perfil="admin", origem=ORIGEM_PAINEL_ADMIN)

    return _pagina_resultado(
        "Canva conectado",
        "O servidor foi autorizado com sucesso. A partir de agora, atestados emitidos com o CPF do "
        "paciente preenchido geram o PDF automaticamente. Você já pode fechar esta janela.",
        sucesso=True,
    )


async def debug_version(request: Request) -> Response:
    """
    GET /admin/canva/debug-version — rota de DIAGNÓSTICO TEMPORÁRIA, criada só
    para investigar um problema de deploy em que as rotas novas do Canva não
    estavam sendo alcançadas em produção (caindo no fallback estático do
    Streamlit). Sem autenticação — não expõe nada sensível, só um marcador
    textual fixo e o SHA do commit (se a plataforma o expuser via env var).
    Remover depois que o problema estiver resolvido.
    """
    return JSONResponse(
        {
            "marcador": "TESTE-V2-CANVA",
            "commit_railway_git_sha": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "desconhecido"),
        }
    )
