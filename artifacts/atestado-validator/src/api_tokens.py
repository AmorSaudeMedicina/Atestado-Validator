"""
api_tokens.py — Geração e verificação de tokens de API por médico.

O token de API é uma credencial sensível: identifica de forma inequívoca
qual médico está fazendo uma chamada programática (nome + CRM), então uma
emissão feita com ele tem o mesmo peso de uma emissão feita pelo formulário.

Guardamos apenas o hash SHA-256 do token no banco (nunca o valor em texto
puro). Diferente de senha de usuário, um token de API é gerado aleatoriamente
com alta entropia (32 bytes ~ 256 bits) — então um hash rápido como SHA-256 já
é seguro contra força bruta, sem precisar do custo computacional do bcrypt
(que existe para proteger senhas curtas escolhidas por humanos).
"""

import hashlib
import secrets

_PREFIXO = "atsd_"


def gerar_token() -> str:
    """Gera um novo token de API em texto puro. Só existe em memória neste momento —
    o chamador deve mostrá-lo uma única vez ao usuário e salvar apenas o hash."""
    return f"{_PREFIXO}{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """Hash determinístico (SHA-256) usado para procurar/comparar o token no banco."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def mascarar_token(ultimos4: str) -> str:
    """Representação segura para exibir na interface (nunca o token completo)."""
    return f"{_PREFIXO}••••••••••••••••{ultimos4}" if ultimos4 else "—"
