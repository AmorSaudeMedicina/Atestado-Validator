"""
server.py — Ponto de entrada do processo.

Substitui o `streamlit run app.py` original: usa a classe App do Streamlit
(baseada em Starlette/ASGI) para servir o app normal e, no MESMO processo e
MESMA porta pública, adicionar as rotas HTTP da API de registro de atestados
(src/api.py). Isso evita ter um segundo serviço/tecnologia separado — a API
roda dentro do próprio app, compartilhando o mesmo banco de dados SQLite.

As configurações de servidor (porta interna, CORS, XSRF, tema) continuam
vindo de .streamlit/config.toml, como antes.
"""

import logging
import os
import re
from pathlib import Path

import uvicorn
from starlette.routing import Route
from streamlit.web.server.starlette import App

from src.database import init_db
from src.api import obter_qr_code, registrar_atestado
from src.mcp_server import mcp_endpoint
from src.oauth_server import (
    autorizar,
    emitir_token,
    metadados_authorization_server,
    metadados_protected_resource,
    registrar_cliente,
)

# O access token OAuth do conector MCP passou a ser enviado apenas no
# cabeçalho Authorization (nunca na URL), então não há mais token embutido
# em caminho de rota para redigir. Este filtro é mantido por precaução: caso
# algum código de autorização de uso único (?code=...) apareça em query
# string durante o redirecionamento OAuth, ele também é mascarado no log —
# um código de autorização de 5 minutos é bem menos sensível que um token de
# longa duração, mas não custa nada reduzir a exposição em log.
_PADRAO_SEGREDO_NA_URL = re.compile(r"([?&](?:code|access_token)=)[^\s\"&]+")


class _RedigirSegredosNosLogsDeAcesso(logging.Filter):
    """Mascara códigos/tokens que apareçam em query string nos logs de acesso do uvicorn."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _PADRAO_SEGREDO_NA_URL.sub(r"\1***", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = _PADRAO_SEGREDO_NA_URL.sub(r"\1***", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedigirSegredosNosLogsDeAcesso())

# Garante que as tabelas (incluindo as novas de OAuth) existam ANTES de
# qualquer rota HTTP ser atendida — não podemos depender de uma sessão do
# Streamlit já ter carregado app.py primeiro, já que a Claude chama estas
# rotas diretamente, sem nunca abrir a página normal do app.
init_db()

_SCRIPT_PATH = str(Path(__file__).resolve().parent / "app.py")

app = App(
    _SCRIPT_PATH,
    routes=[
        Route("/api/atestados", registrar_atestado, methods=["POST"]),
        Route("/api/atestados/{codigo}/qrcode.png", obter_qr_code, methods=["GET"]),
        Route("/mcp", mcp_endpoint, methods=["GET", "POST"]),
        Route("/.well-known/oauth-authorization-server", metadados_authorization_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", metadados_protected_resource, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", metadados_protected_resource, methods=["GET"]),
        Route("/oauth/register", registrar_cliente, methods=["POST"]),
        Route("/oauth/authorize", autorizar, methods=["GET", "POST"]),
        Route("/oauth/token", emitir_token, methods=["POST"]),
    ],
)

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="info")
