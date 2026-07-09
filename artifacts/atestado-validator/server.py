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

from src.api import obter_qr_code, registrar_atestado
from src.mcp_server import mcp_endpoint

_PADRAO_TOKEN_NA_URL = re.compile(r"(/mcp/)[^\s\"?/]+")


class _RedigirTokenNosLogsDeAcesso(logging.Filter):
    """
    O token de API do médico fica embutido na própria URL do conector MCP
    (/mcp/{token}), então cada chamada aparece inteira no log de acesso do
    uvicorn. Este filtro substitui o token por "***" antes de logar, para não
    deixar a credencial gravada em texto puro nos logs do servidor.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _PADRAO_TOKEN_NA_URL.sub(r"\1***", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = _PADRAO_TOKEN_NA_URL.sub(r"\1***", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedigirTokenNosLogsDeAcesso())

_SCRIPT_PATH = str(Path(__file__).resolve().parent / "app.py")

app = App(
    _SCRIPT_PATH,
    routes=[
        Route("/api/atestados", registrar_atestado, methods=["POST"]),
        Route("/api/atestados/{codigo}/qrcode.png", obter_qr_code, methods=["GET"]),
        Route("/mcp/{token}", mcp_endpoint, methods=["GET", "POST"]),
    ],
)

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="info")
