"""
urls.py — Monta URLs públicas do app a partir do domínio real da requisição.

Compartilhado entre o app Streamlit (app.py), a API HTTP (src/api.py) e o
conector MCP/OAuth (src/mcp_server.py, src/oauth_server.py) para garantir que
o link de verificação, o QR Code e os endpoints OAuth sempre apontem para o
MESMO domínio que o chamador de fato usou — funciona tanto no domínio de
desenvolvimento (`*.replit.dev`) quanto no domínio publicado (`*.replit.app`
ou um domínio customizado), sem precisar trocar nada manualmente ao publicar.

Prioridade para descobrir o domínio, da mais para a menos confiável:
1. Uma requisição HTTP explícita (Starlette `Request`, passada por quem chama)
   — lida o host real através de `X-Forwarded-Host`/`Host` (o proxy do Replit
   sempre preenche esses cabeçalhos corretamente, tanto em dev quanto em
   produção).
2. O contexto da página Streamlit (`st.context.url`) — usado quando o código
   roda dentro do script do app (ex.: `app.py`) sem acesso direto ao objeto
   `Request` do Starlette.
3. Variáveis de ambiente do Replit (`REPLIT_DEV_DOMAIN`), só como último
   recurso para contextos sem requisição alguma (ex.: um script batch).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from starlette.requests import Request

# Sufixos de domínio confiáveis para aceitar via cabeçalho (dev e produção no
# Replit). Um domínio customizado, se o usuário configurar um na publicação,
# pode ser adicionado aqui através da env var PUBLIC_HOST_SUFFIXES_EXTRA
# (lista separada por vírgula, ex.: "meudominio.com.br").
_SUFIXOS_HOST_CONFIAVEIS = (".replit.dev", ".replit.app", "localhost", "127.0.0.1")

# Formato aceitável de host: letras/números/hífen/ponto, opcionalmente com
# porta — nunca espaços, vírgulas, barras, ou outros caracteres que
# permitiriam contrabando de cabeçalho ou apontar para um caminho/URL.
_PADRAO_HOST_VALIDO = re.compile(r"^[a-zA-Z0-9.-]+(:\d{1,5})?$")


def _hosts_extra_confiaveis() -> tuple[str, ...]:
    bruto = os.environ.get("PUBLIC_HOST_SUFFIXES_EXTRA", "")
    return tuple(h.strip() for h in bruto.split(",") if h.strip())


def _host_e_confiavel(host: str) -> bool:
    """
    Só aceita hosts que batem com um domínio conhecido do Replit (dev/prod)
    ou um domínio customizado explicitamente liberado via env var — nunca um
    valor arbitrário vindo de `Host`/`X-Forwarded-Host`. Isso evita que um
    cabeçalho forjado (host header injection) seja usado para gerar QR Codes,
    links de verificação, ou metadados OAuth (`issuer`, `authorization_endpoint`)
    apontando para um domínio controlado por um atacante.
    """
    if not _PADRAO_HOST_VALIDO.match(host):
        return False
    host_sem_porta = host.split(":", 1)[0].lower()
    sufixos = _SUFIXOS_HOST_CONFIAVEIS + _hosts_extra_confiaveis()
    return any(
        host_sem_porta == sufixo.lstrip(".").lower() or host_sem_porta.endswith(sufixo.lower())
        for sufixo in sufixos
    )


def _base_a_partir_da_requisicao(request: "Request") -> str | None:
    proto_bruto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    # Proxies podem encadear múltiplos valores separados por vírgula
    # (`X-Forwarded-Proto: https, http`); o primeiro é o do cliente original.
    proto = proto_bruto.split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        proto = "https"

    host_bruto = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host_bruto:
        host_bruto = request.url.netloc
    if not host_bruto:
        return None
    host = host_bruto.split(",")[0].strip()

    if not _host_e_confiavel(host):
        return None

    return f"{proto}://{host}/"


def _base_a_partir_do_contexto_streamlit() -> str | None:
    try:
        import streamlit as st

        ctx_url = st.context.url
    except Exception:
        return None
    if not ctx_url:
        return None
    partes = urlparse(ctx_url)
    if not partes.scheme or not partes.netloc:
        return None
    return f"{partes.scheme}://{partes.netloc}/"


def url_base(request: "Request | None" = None) -> str:
    """
    Monta a URL base pública do app.

    Passe `request` sempre que estiver dentro de um handler HTTP (Starlette) —
    é a fonte mais confiável. Sem `request`, tenta o contexto da página
    Streamlit; se nada disso estiver disponível, cai para as variáveis de
    ambiente do Replit (dev) ou localhost (execução local avulsa).
    """
    if request is not None:
        base = _base_a_partir_da_requisicao(request)
        if base:
            return base

    base = _base_a_partir_do_contexto_streamlit()
    if base:
        return base

    dominio = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if dominio:
        return f"https://{dominio}/"

    return "http://localhost:5000/"


def url_verificacao(codigo: str, request: "Request | None" = None) -> str:
    """URL pública da página de verificação de um atestado."""
    return f"{url_base(request)}?codigo={codigo}"


def url_qr_publica(codigo: str, request: "Request | None" = None) -> str:
    """URL pública da imagem PNG do QR Code de um atestado (para uso por sistemas externos)."""
    return f"{url_base(request)}api/atestados/{codigo}/qrcode.png"
