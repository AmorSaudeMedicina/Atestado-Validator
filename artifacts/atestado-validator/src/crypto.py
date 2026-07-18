"""
crypto.py — Criptografia em nível de campo (application-level) para dados
sensíveis do atestado (nome do paciente, CID) em repouso no banco SQLite.

Usa Fernet — da biblioteca `cryptography` (PyCA), AES-128-CBC + HMAC-SHA256
autenticado — com uma chave simétrica lida OBRIGATORIAMENTE da variável de
ambiente ENCRYPTION_KEY. A chave NUNCA fica no código.

Fail-closed por design: se ENCRYPTION_KEY não estiver definida, ou não for
uma chave Fernet válida, `_fernet()` levanta `ChaveDeCriptografiaAusente` —
e `src.database.init_db()` chama essa função logo na subida do processo
(antes de qualquer leitura/gravação de atestado), então o app se recusa a
rodar sem criptografia configurada, em vez de gravar dados sensíveis em
texto puro "por acidente".
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_COMANDO_GERAR_CHAVE = (
    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)


class ChaveDeCriptografiaAusente(RuntimeError):
    """
    Levantado quando ENCRYPTION_KEY não está definida ou não é uma chave
    Fernet válida — falha fail-closed já na subida do processo (ver
    `src.database.init_db()`), nunca em silêncio no meio de uma requisição.
    """


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    chave = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not chave:
        raise ChaveDeCriptografiaAusente(
            "ENCRYPTION_KEY nao definida. Por seguranca (LGPD), o app se recusa a "
            "subir sem uma chave de criptografia configurada — dados de pacientes "
            "(nome, CID) nunca podem ser gravados em texto puro por acidente.\n"
            "Gere uma chave valida com:\n"
            f"  {_COMANDO_GERAR_CHAVE}\n"
            "e defina ENCRYPTION_KEY com o valor gerado (variavel de ambiente, "
            "NUNCA no codigo). Guarde essa chave em local seguro: se ela for "
            "perdida ou trocada, os atestados ja gravados ficam IRRECUPERAVEIS."
        )
    try:
        return Fernet(chave.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ChaveDeCriptografiaAusente(
            "ENCRYPTION_KEY esta definida mas nao e uma chave Fernet valida "
            "(precisa ser uma string base64 urlsafe de 32 bytes, gerada por "
            f"Fernet.generate_key()). Gere uma nova com:\n  {_COMANDO_GERAR_CHAVE}"
        ) from exc


def validar_chave_na_subida() -> None:
    """
    Força a validação de ENCRYPTION_KEY imediatamente — chamada por
    `src.database.init_db()` na subida do processo (server.py e app.py),
    para que uma configuração ausente/errada derrube o processo já no boot
    (fail-closed), em vez de só falhar na primeira vez que um atestado for
    lido ou gravado.
    """
    _fernet()


def criptografar(texto_puro: Optional[str]) -> Optional[str]:
    """Criptografa uma string com a chave de ENCRYPTION_KEY. `None` permanece `None`."""
    if texto_puro is None:
        return None
    return _fernet().encrypt(texto_puro.encode("utf-8")).decode("utf-8")


def descriptografar(texto_cifrado: Optional[str]) -> Optional[str]:
    """
    Descriptografa uma string cifrada por `criptografar()`. `None` permanece
    `None`. Levanta `InvalidToken` se o valor não foi cifrado com a chave
    atual (ex.: ENCRYPTION_KEY trocada) — nunca retorna dado corrompido em
    silêncio.
    """
    if texto_cifrado is None:
        return None
    return _fernet().decrypt(texto_cifrado.encode("utf-8")).decode("utf-8")
