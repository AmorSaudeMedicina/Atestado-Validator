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
import threading
import time as _time
from pathlib import Path

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from streamlit.web.server.starlette import App

from src.audit import limpar_eventos_antigos
from src.auth import semear_usuarios_iniciais
from src.canva_admin import callback_canva, conectar_canva, debug_dataset, debug_version
from src.database import init_db
from src.api import obter_qr_code, registrar_atestado
from src.retencao import aplicar_retencao_automatica
from src.mcp_server import mcp_endpoint
from src.oauth_server import (
    autorizar,
    emitir_token,
    metadados_authorization_server,
    metadados_protected_resource,
    registrar_cliente,
)


async def healthz(request: Request) -> Response:
    """
    GET /healthz — usado pelo Replit (e por qualquer monitor externo) para
    verificar se o processo está de pé e o banco de dados está acessível.
    Não requer autenticação nem toca em dados de médico/paciente.
    """
    try:
        init_db()
        return JSONResponse({"status": "ok"})
    except Exception:
        return JSONResponse({"status": "erro"}, status_code=503)

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

# Cria o administrador inicial e os médicos de teste já na subida do
# processo (não apenas quando alguém abre a UI pela primeira vez) — em um
# banco novo (ex.: Volume vazio no primeiro deploy do Railway) isso garante
# que sempre existe uma conta para logar. Idempotente: se o banco já tem
# alguma conta (mesmo em reinícios seguintes), não faz nada.
semear_usuarios_iniciais()

# Retenção da trilha de auditoria (AUDIT_RETENTION_DAYS, padrão 365 dias):
# limpa já na subida (cobre reinícios/deploys) e depois a cada 24h em uma
# thread em segundo plano, para processos de longa duração entre deploys —
# ver src/audit.py. Nunca levanta exceção (nem aqui nem dentro da thread).
limpar_eventos_antigos()

_INTERVALO_LIMPEZA_AUDITORIA_SEGUNDOS = 24 * 60 * 60


def _loop_limpeza_auditoria() -> None:
    while True:
        _time.sleep(_INTERVALO_LIMPEZA_AUDITORIA_SEGUNDOS)
        limpar_eventos_antigos()


threading.Thread(target=_loop_limpeza_auditoria, daemon=True, name="limpeza-auditoria").start()

# Retenção/exclusão de dados dos atestados (LGPD/segurança, parte 4):
# ATESTADO_RETENTION_DAYS é opt-in e vem DESLIGADA por padrão — se ausente,
# aplicar_retencao_automatica() não faz nada. Quando ligada, só anonimiza
# (nunca exclui), na subida (cobre reinícios/deploys) e depois a cada 24h em
# segundo plano — ver src/retencao.py. Nunca levanta exceção.
aplicar_retencao_automatica()

_INTERVALO_RETENCAO_ATESTADOS_SEGUNDOS = 24 * 60 * 60


def _loop_retencao_atestados() -> None:
    while True:
        _time.sleep(_INTERVALO_RETENCAO_ATESTADOS_SEGUNDOS)
        aplicar_retencao_automatica()


threading.Thread(target=_loop_retencao_atestados, daemon=True, name="retencao-atestados").start()

_SCRIPT_PATH = str(Path(__file__).resolve().parent / "app.py")

app = App(
    _SCRIPT_PATH,
    routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Route("/atestados", registrar_atestado, methods=["POST"]),
        Route("/atestados/{codigo}/qrcode.png", obter_qr_code, methods=["GET", "OPTIONS"]),
        Route("/mcp", mcp_endpoint, methods=["GET", "POST"]),
        Route("/.well-known/oauth-authorization-server", metadados_authorization_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", metadados_protected_resource, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", metadados_protected_resource, methods=["GET"]),
        Route("/oauth/register", registrar_cliente, methods=["POST"]),
        Route("/oauth/authorize", autorizar, methods=["GET", "POST"]),
        Route("/oauth/token", emitir_token, methods=["POST"]),
        Route("/admin/canva/conectar", conectar_canva, methods=["GET", "POST"]),
        Route("/admin/canva/callback", callback_canva, methods=["GET"]),
        Route("/admin/canva/debug-version", debug_version, methods=["GET"]),
        Route("/admin/canva/debug-dataset", debug_dataset, methods=["GET"]),
    ],
)

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="info")
