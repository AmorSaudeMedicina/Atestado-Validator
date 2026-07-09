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

import os
from pathlib import Path

import uvicorn
from starlette.routing import Route
from streamlit.web.server.starlette import App

from src.api import obter_qr_code, registrar_atestado

_SCRIPT_PATH = str(Path(__file__).resolve().parent / "app.py")

app = App(
    _SCRIPT_PATH,
    routes=[
        Route("/api/atestados", registrar_atestado, methods=["POST"]),
        Route("/api/atestados/{codigo}/qrcode.png", obter_qr_code, methods=["GET"]),
    ],
)

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="info")
