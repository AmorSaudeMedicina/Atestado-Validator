"""
urls.py — Monta URLs públicas do app a partir do domínio do Replit.

Compartilhado entre o app Streamlit (app.py) e a API HTTP (src/api.py) para
garantir que o link de verificação e o link do QR Code sempre apontem para o
mesmo domínio público, não importa quem os gerou.
"""

import os


def url_base() -> str:
    """Monta a URL base do app a partir da variável de ambiente do Replit."""
    dominio = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if dominio:
        return f"https://{dominio}/"
    # Fallback para ambiente local
    return "http://localhost:5000/"


def url_verificacao(codigo: str) -> str:
    """URL pública da página de verificação de um atestado."""
    return f"{url_base()}?codigo={codigo}"


def url_qr_publica(codigo: str) -> str:
    """URL pública da imagem PNG do QR Code de um atestado (para uso por sistemas externos)."""
    return f"{url_base()}api/atestados/{codigo}/qrcode.png"
