"""
audit.py — Trilha de auditoria (LGPD/segurança, parte 3).

Registra QUEM fez O QUE e QUANDO (emissão e revogação de atestado, login
bem-sucedido/falho/bloqueado, ações do admin sobre contas de médico), para
rastreabilidade e prestação de contas.

Princípios:
- NUNCA grava dado sensível de paciente: um atestado é referenciado só pelo
  `atestado_codigo` (não sensível — é o mesmo código já público usado na
  verificação), nunca por nome de paciente ou CID.
- A página pública de verificação (`tela_verificacao`) é intencionalmente
  ANÔNIMA e NÃO gera evento nenhum aqui — quem consulta um atestado pelo QR
  não deve ter isso registrado, por design.
- `registrar_evento()` NUNCA levanta exceção: se a gravação falhar (ex.:
  banco indisponível), a falha só é registrada no log de processo — a
  operação principal que chamou (login, emissão, ação do admin) já
  terminou e não deve ser desfeita nem impedida por uma falha de auditoria.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from src.database import (
    inserir_evento_auditoria,
    limpar_eventos_auditoria_antigos,
)

_LOGGER = logging.getLogger("amorsaude.audit")

# ---------------------------------------------------------------------------
# Tipos de evento conhecidos — string livre no banco, mas centralizados aqui
# para consistência entre quem grava e o filtro da tela de auditoria.
# ---------------------------------------------------------------------------
EVENTO_ATESTADO_EMITIDO = "atestado_emitido"
EVENTO_ATESTADO_REVOGADO = "atestado_revogado"
EVENTO_LOGIN_SUCESSO = "login_sucesso"
EVENTO_LOGIN_FALHA = "login_falha"
EVENTO_LOGIN_BLOQUEADO = "login_bloqueado"
EVENTO_MEDICO_CRIADO = "medico_criado"
EVENTO_MEDICO_ATIVADO = "medico_ativado"
EVENTO_MEDICO_DESATIVADO = "medico_desativado"
EVENTO_SENHA_REDEFINIDA_ADMIN = "senha_redefinida_por_admin"
EVENTO_SENHA_TROCADA_PROPRIA = "senha_trocada_propria"

TODOS_OS_TIPOS_DE_EVENTO = [
    EVENTO_ATESTADO_EMITIDO,
    EVENTO_ATESTADO_REVOGADO,
    EVENTO_LOGIN_SUCESSO,
    EVENTO_LOGIN_FALHA,
    EVENTO_LOGIN_BLOQUEADO,
    EVENTO_MEDICO_CRIADO,
    EVENTO_MEDICO_ATIVADO,
    EVENTO_MEDICO_DESATIVADO,
    EVENTO_SENHA_REDEFINIDA_ADMIN,
    EVENTO_SENHA_TROCADA_PROPRIA,
]

# Rótulos em português para exibir na tela de auditoria (select de filtro e lista de eventos).
RÓTULOS_TIPOS_DE_EVENTO = {
    EVENTO_ATESTADO_EMITIDO: "Atestado emitido",
    EVENTO_ATESTADO_REVOGADO: "Atestado revogado",
    EVENTO_LOGIN_SUCESSO: "Login bem-sucedido",
    EVENTO_LOGIN_FALHA: "Login falho",
    EVENTO_LOGIN_BLOQUEADO: "Conta bloqueada (tentativas)",
    EVENTO_MEDICO_CRIADO: "Médico criado",
    EVENTO_MEDICO_ATIVADO: "Médico ativado",
    EVENTO_MEDICO_DESATIVADO: "Médico desativado",
    EVENTO_SENHA_REDEFINIDA_ADMIN: "Senha redefinida pelo admin",
    EVENTO_SENHA_TROCADA_PROPRIA: "Senha trocada (pela própria conta)",
}

# Origens conhecidas de uma ação — de onde ela partiu.
ORIGEM_FORMULARIO = "formulario"
ORIGEM_API = "api"
ORIGEM_MCP = "mcp"
ORIGEM_PAINEL_ADMIN = "painel_admin"

_RETENCAO_PADRAO_DIAS = 365


def registrar_evento(
    tipo_evento: str,
    *,
    ator_usuario: Optional[str] = None,
    ator_perfil: Optional[str] = None,
    atestado_codigo: Optional[str] = None,
    origem: Optional[str] = None,
    detalhe: Optional[str] = None,
) -> None:
    """
    Grava um evento na trilha de auditoria. NUNCA levanta exceção — se a
    gravação falhar, a falha é só registrada no log de processo; quem
    chamou (login, emissão, ação do admin) já terminou sua operação
    principal e não deve ser afetado por uma falha aqui.

    `detalhe` deve conter só informação operacional curta e não sensível
    (ex.: usuário do médico afetado por uma ação do admin) — NUNCA nome de
    paciente nem CID; um atestado é sempre referenciado só por
    `atestado_codigo`.
    """
    try:
        inserir_evento_auditoria(
            tipo_evento=tipo_evento,
            ator_usuario=ator_usuario,
            ator_perfil=ator_perfil,
            atestado_codigo=atestado_codigo,
            origem=origem,
            detalhe=detalhe,
        )
    except Exception:
        _LOGGER.error(
            "Falha ao gravar evento de auditoria (tipo=%s, ator=%s) — a operacao "
            "principal NAO foi afetada, mas este evento de auditoria nao ficou registrado.",
            tipo_evento,
            ator_usuario,
            exc_info=True,
        )


def dias_retencao_configurados() -> int:
    """Lê AUDIT_RETENTION_DAYS (dias de retenção do log de auditoria); usa um padrão de 365 dias se ausente/inválida."""
    bruto = os.environ.get("AUDIT_RETENTION_DAYS", "").strip()
    if not bruto:
        return _RETENCAO_PADRAO_DIAS
    try:
        dias = int(bruto)
    except ValueError:
        return _RETENCAO_PADRAO_DIAS
    return dias if dias > 0 else _RETENCAO_PADRAO_DIAS


def limpar_eventos_antigos() -> int:
    """
    Remove eventos de auditoria mais antigos que `AUDIT_RETENTION_DAYS` dias
    (padrão 365). Chamada na subida do processo (server.py/app.py) e
    periodicamente em segundo plano por server.py, para processos de longa
    duração entre deploys. NUNCA levanta exceção.
    """
    try:
        return limpar_eventos_auditoria_antigos(dias_retencao_configurados())
    except Exception:
        _LOGGER.error("Falha ao limpar eventos antigos de auditoria.", exc_info=True)
        return 0
