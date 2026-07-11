"""
oauth_server.py — Servidor de autorização OAuth 2.0 mínimo (Dynamic Client
Registration + Authorization Code + PKCE), usado exclusivamente para
autenticar o conector MCP.

Por que isto existe: a Claude não expõe, para conectores normais, um campo
simples para colar um token fixo em cabeçalho (esse recurso — "Request
headers" — está em beta e é liberado só por solicitação à Anthropic). O
mecanismo de autenticação que a Claude sempre suporta "de fábrica" para
conectores remotos é o fluxo OAuth 2.0 com Dynamic Client Registration (DCR):
a própria Claude descobre este servidor, se registra automaticamente, abre a
tela de login abaixo para o médico autorizar, e passa a enviar o access token
resultante em `Authorization: Bearer <token>` em toda chamada ao conector.
Isso substitui o token-na-URL usado antes, que a Claude conectava mas não
listava as ferramentas (sintoma de que o handshake dela não reconhecia aquele
esquema como uma forma de autenticação válida).

Nenhuma senha é processada aqui de forma diferente do login normal do app —
`_secao_login` e este módulo compartilham a mesma função `autenticar()`
(bcrypt) de `src/auth.py`. O que muda é apenas o formato do resultado: em vez
de uma sessão Streamlit, o resultado é um código de autorização de uso único
trocado por um access token opaco (mesmo padrão de token de API já usado em
`src/api_tokens.py`, só que aqui guardado em uma tabela própria com validade).
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
from urllib.parse import urlencode, urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from src.api_tokens import hash_token
from src.auth import autenticar
from src.database import (
    buscar_oauth_client,
    consumir_oauth_auth_code,
    criar_oauth_access_token,
    criar_oauth_auth_code,
    criar_oauth_client,
)
from src.urls import url_base

_DURACAO_ACCESS_TOKEN_DIAS = 180


def _emissor() -> str:
    return url_base().rstrip("/")


def _redirect_uri_valida(uri: str) -> bool:
    """
    Exige uma URL absoluta HTTPS (ou http://localhost/127.0.0.1, útil para
    testes locais de um cliente MCP) — nunca um esquema arbitrário
    (javascript:, data:, etc.) nem uma URL relativa. Isso impede que o DCR
    seja usado para transformar `/oauth/authorize` num open redirect: mesmo
    que qualquer um possa registrar um client_id (padrão do DCR), o destino
    do redirecionamento sempre precisa ser um endereço HTTPS real (o do
    próprio cliente MCP, ex.: claude.ai), nunca algo que abra uma página
    controlada por outro esquema/local malicioso.
    """
    try:
        partes = urlparse(uri)
    except ValueError:
        return False
    if partes.scheme == "https" and partes.netloc:
        return True
    if partes.scheme == "http" and partes.hostname in ("localhost", "127.0.0.1"):
        return True
    return False


def _erro_json(status: int, erro: str, descricao: str = "") -> JSONResponse:
    corpo = {"error": erro}
    if descricao:
        corpo["error_description"] = descricao
    return JSONResponse(corpo, status_code=status)


async def metadados_authorization_server(request: Request) -> Response:
    """GET /.well-known/oauth-authorization-server — descoberta do servidor de autorização (RFC 8414)."""
    emissor = _emissor()
    return JSONResponse(
        {
            "issuer": emissor,
            "authorization_endpoint": f"{emissor}/oauth/authorize",
            "token_endpoint": f"{emissor}/oauth/token",
            "registration_endpoint": f"{emissor}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )


async def metadados_protected_resource(request: Request) -> Response:
    """GET /.well-known/oauth-protected-resource[/mcp] — metadados do recurso protegido (RFC 9728)."""
    emissor = _emissor()
    return JSONResponse(
        {
            "resource": f"{emissor}/mcp",
            "authorization_servers": [emissor],
        }
    )


async def registrar_cliente(request: Request) -> Response:
    """
    POST /oauth/register — Dynamic Client Registration (RFC 7591).

    Sem autenticação prévia: é assim que a Claude se registra automaticamente
    na primeira conexão de um conector, sem exigir nenhuma configuração manual.
    Não emitimos client_secret (cliente público, protegido por PKCE).
    """
    try:
        corpo = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _erro_json(400, "invalid_client_metadata", "JSON inválido.")

    if not isinstance(corpo, dict):
        return _erro_json(400, "invalid_client_metadata", "Corpo deve ser um objeto JSON.")

    redirect_uris = corpo.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris or not all(isinstance(u, str) for u in redirect_uris):
        return _erro_json(400, "invalid_redirect_uri", "Campo 'redirect_uris' é obrigatório (lista de URLs).")
    if not all(_redirect_uri_valida(u) for u in redirect_uris):
        return _erro_json(
            400,
            "invalid_redirect_uri",
            "Cada 'redirect_uri' deve ser uma URL absoluta https:// (ou http://localhost para testes).",
        )

    client_id = secrets.token_urlsafe(16)
    nome_cliente = str(corpo.get("client_name") or "Cliente MCP")[:200]
    criar_oauth_client(client_id, nome_cliente, json.dumps(redirect_uris))

    return JSONResponse(
        {
            "client_id": client_id,
            "client_name": nome_cliente,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
        status_code=201,
    )


def _validar_cliente_e_redirect(client_id: str, redirect_uri: str) -> tuple[dict | None, str | None]:
    cliente = buscar_oauth_client(client_id) if client_id else None
    if not cliente:
        return None, "Cliente OAuth desconhecido. Remova e adicione o conector novamente na Claude."
    # Defesa em profundidade: revalida o formato aqui também, não só no
    # registro — assim, mesmo que um registro antigo/inválido exista na
    # tabela por algum motivo, o redirecionamento nunca é feito para um
    # esquema/URL fora do permitido.
    if not _redirect_uri_valida(redirect_uri):
        return None, "Endereço de retorno (redirect_uri) inválido."
    uris_registradas = json.loads(cliente["redirect_uris"])
    if redirect_uri not in uris_registradas:
        return None, "Endereço de retorno (redirect_uri) não corresponde ao registrado para este cliente."
    return cliente, None


def _pagina_login(*, params: dict, nome_cliente: str, erro: str | None = None) -> HTMLResponse:
    campos_ocultos = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in params.items()
    )
    aviso_erro = f'<p style="color:#c0392b;font-weight:600">{html.escape(erro)}</p>' if erro else ""
    pagina = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Autorizar conector — AmorSaúde</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: system-ui, sans-serif; background:#eef6f8; display:flex; justify-content:center;
         padding-top:8vh; color:#0f3d47; }}
  .cartao {{ background:#fff; border-radius:16px; padding:32px; max-width:380px; width:100%;
            box-shadow:0 4px 24px rgba(0,0,0,.08); }}
  h1 {{ font-size:1.25rem; margin-bottom:4px; }}
  p.aviso {{ font-size:.92rem; color:#456; margin-top:0; }}
  label {{ display:block; margin-top:14px; font-size:.9rem; font-weight:600; }}
  input[type=text], input[type=password] {{ width:100%; padding:10px; margin-top:4px; border-radius:8px;
            border:1px solid #cdd; font-size:1rem; box-sizing:border-box; }}
  button {{ margin-top:20px; width:100%; padding:11px; border-radius:8px; border:none; background:#e2434d;
            color:#fff; font-size:1rem; font-weight:600; cursor:pointer; }}
</style>
</head>
<body>
  <div class="cartao">
    <h1>🔐 Autorizar conector</h1>
    <p class="aviso"><strong>{html.escape(nome_cliente)}</strong> está solicitando permissão para
       registrar atestados em seu nome, usando sua conta de médico do AmorSaúde.</p>
    {aviso_erro}
    <form method="post">
      {campos_ocultos}
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


async def autorizar(request: Request) -> Response:
    """
    GET/POST /oauth/authorize — tela de login e consentimento do médico.

    GET: mostra o formulário de login (a Claude abre esta URL num navegador).
    POST: valida usuário/senha (mesma função do login normal do app) e, se
    a conta for de um médico ativo, gera o código de autorização e redireciona
    de volta para o cliente (redirect_uri), exatamente como um "Entrar com..."
    de terceiros.
    """
    if request.method == "GET":
        params = dict(request.query_params)
    else:
        form = await request.form()
        params = dict(form)

    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "")
    state = params.get("state", "")
    response_type = params.get("response_type", "code")

    if response_type != "code" or not code_challenge or code_challenge_method != "S256":
        return HTMLResponse(
            "<p>Requisição de autorização inválida ou sem PKCE (code_challenge_method deve ser S256).</p>",
            status_code=400,
        )

    cliente, erro_cliente = _validar_cliente_e_redirect(client_id, redirect_uri)
    if erro_cliente:
        return HTMLResponse(f"<p>{html.escape(erro_cliente)}</p>", status_code=400)

    campos_para_reenviar = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "state": state,
        "response_type": response_type,
    }

    if request.method == "GET":
        return _pagina_login(params=campos_para_reenviar, nome_cliente=cliente["client_name"] or "Cliente MCP")

    usuario = str(params.get("usuario") or "").strip()
    senha = str(params.get("senha") or "")
    conta = autenticar(usuario, senha)
    if not conta:
        return _pagina_login(
            params=campos_para_reenviar,
            nome_cliente=cliente["client_name"] or "Cliente MCP",
            erro="Usuário ou senha incorretos.",
        )
    if conta["perfil"] != "medico":
        return _pagina_login(
            params=campos_para_reenviar,
            nome_cliente=cliente["client_name"] or "Cliente MCP",
            erro="Apenas contas de médico podem autorizar este conector.",
        )

    codigo = secrets.token_urlsafe(32)
    criar_oauth_auth_code(
        codigo=codigo,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        usuario_id=conta["id"],
    )

    query = {"code": codigo}
    if state:
        query["state"] = state
    return RedirectResponse(url=f"{redirect_uri}?{urlencode(query)}", status_code=302)


def _verificar_pkce(code_verifier: str, code_challenge: str) -> bool:
    if not code_verifier:
        return False
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    calculado = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(calculado, code_challenge)


async def emitir_token(request: Request) -> Response:
    """POST /oauth/token — troca o código de autorização (+ PKCE) por um access token."""
    content_type = request.headers.get("content-type", "")
    try:
        if "application/json" in content_type:
            corpo = await request.json()
        else:
            corpo = dict(await request.form())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _erro_json(400, "invalid_request", "Corpo da requisição inválido.")

    if corpo.get("grant_type") != "authorization_code":
        return _erro_json(400, "unsupported_grant_type", "Apenas 'authorization_code' é suportado.")

    codigo = str(corpo.get("code") or "")
    client_id = str(corpo.get("client_id") or "")
    redirect_uri = str(corpo.get("redirect_uri") or "")
    code_verifier = str(corpo.get("code_verifier") or "")

    registro_codigo = consumir_oauth_auth_code(codigo)
    if not registro_codigo:
        return _erro_json(400, "invalid_grant", "Código de autorização inválido, expirado ou já utilizado.")

    if registro_codigo["client_id"] != client_id or registro_codigo["redirect_uri"] != redirect_uri:
        return _erro_json(400, "invalid_grant", "client_id ou redirect_uri não correspondem ao código emitido.")

    if not _verificar_pkce(code_verifier, registro_codigo["code_challenge"]):
        return _erro_json(400, "invalid_grant", "Verificação PKCE (code_verifier) falhou.")

    novo_token = secrets.token_urlsafe(32)
    criar_oauth_access_token(
        token_hash=hash_token(novo_token),
        usuario_id=registro_codigo["usuario_id"],
        client_id=client_id,
        dias_validade=_DURACAO_ACCESS_TOKEN_DIAS,
    )

    return JSONResponse(
        {
            "access_token": novo_token,
            "token_type": "Bearer",
            "expires_in": _DURACAO_ACCESS_TOKEN_DIAS * 24 * 60 * 60,
        }
    )
