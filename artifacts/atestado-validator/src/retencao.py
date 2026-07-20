"""
retencao.py — Retenção e exclusão de dados dos atestados (LGPD/segurança, parte 4).

Duas frentes:

1) Ferramenta MANUAL (só admin, no painel): localizar um atestado pelo
   código e ANONIMIZAR ou EXCLUIR definitivamente — usada para atender um
   pedido de titular (direito de exclusão da LGPD). Ver tela_retencao() em
   app.py.

2) Retenção AUTOMÁTICA opt-in (variável ATESTADO_RETENTION_DAYS),
   DESLIGADA por padrão: se a variável estiver ausente/vazia/0, esta parte
   não apaga nem anonimiza nada. Se definida com um número de dias > 0,
   ANONIMIZA (nunca exclui) os atestados mais antigos que esse prazo, na
   subida do processo e periodicamente (ver server.py).

O prazo de retenção em si é decisão jurídica — registros médicos costumam
exigir guarda longa. Por isso a automação é conservadora por padrão (nada
acontece sem configuração explícita) e, quando ligada, só anonimiza
(preserva os campos operacionais: código, datas, período, status), nunca
exclui.

Segue os mesmos princípios de src/audit.py:
- NUNCA loga dado sensível — o evento de auditoria referencia o atestado só
  pelo `atestado_codigo` (mesmo código já público da verificação).
- A rotina automática NUNCA levanta exceção nem derruba a aplicação: se
  falhar, registra no log de processo e segue.
"""

from __future__ import annotations

import logging
import os

from src.audit import (
    EVENTO_ATESTADO_ANONIMIZADO,
    EVENTO_ATESTADO_EXCLUIDO,
    ORIGEM_RETENCAO_AUTOMATICA,
    registrar_evento,
)
from src.database import (
    anonimizar_atestado,
    excluir_atestado_definitivamente,
    listar_codigos_atestados_para_retencao,
)

_LOGGER = logging.getLogger("amorsaude.retencao")

_VARIAVEL_RETENCAO = "ATESTADO_RETENTION_DAYS"


def anonimizar_atestado_manual(codigo: str, *, ator_usuario: str, ator_perfil: str, origem: str) -> bool:
    """
    Anonimiza um atestado a pedido do admin (ex.: solicitação de exclusão do
    titular). Grava o evento na auditoria só se algo foi de fato alterado.
    Retorna True se anonimizou agora.
    """
    anonimizado = anonimizar_atestado(codigo)
    if anonimizado:
        registrar_evento(
            EVENTO_ATESTADO_ANONIMIZADO,
            ator_usuario=ator_usuario,
            ator_perfil=ator_perfil,
            atestado_codigo=codigo,
            origem=origem,
        )
    return anonimizado


def excluir_atestado_manual(codigo: str, *, ator_usuario: str, ator_perfil: str, origem: str) -> bool:
    """
    Exclui definitivamente um atestado a pedido do admin. Grava o evento na
    auditoria (só o código — o dado sensível já não existe mais em lugar
    nenhum depois desta chamada). Retorna True se excluiu agora.
    """
    excluido = excluir_atestado_definitivamente(codigo)
    if excluido:
        registrar_evento(
            EVENTO_ATESTADO_EXCLUIDO,
            ator_usuario=ator_usuario,
            ator_perfil=ator_perfil,
            atestado_codigo=codigo,
            origem=origem,
        )
    return excluido


def dias_retencao_atestados_configurados() -> int:
    """
    Lê ATESTADO_RETENTION_DAYS. Retorna 0 se ausente/vazia/inválida/≤0 — o
    que significa "retenção automática DESLIGADA" (padrão conservador: nada
    é apagado ou anonimizado automaticamente sem essa configuração explícita).
    """
    bruto = os.environ.get(_VARIAVEL_RETENCAO, "").strip()
    if not bruto:
        return 0
    try:
        dias = int(bruto)
    except ValueError:
        return 0
    return dias if dias > 0 else 0


def aplicar_retencao_automatica() -> int:
    """
    Se ATESTADO_RETENTION_DAYS estiver configurada (> 0), anonimiza (nunca
    exclui) os atestados emitidos há mais tempo que esse prazo. Se a
    variável estiver ausente/vazia/0, não faz nada — comportamento padrão.

    Chamada na subida do processo e periodicamente em segundo plano (ver
    server.py/app.py), para processos de longa duração entre deploys. NUNCA
    levanta exceção — se falhar, registra no log de processo e segue, sem
    derrubar a aplicação. Retorna quantos atestados foram anonimizados
    nesta chamada.
    """
    dias = dias_retencao_atestados_configurados()
    if dias <= 0:
        return 0
    try:
        codigos = listar_codigos_atestados_para_retencao(dias)
        total_anonimizados = 0
        for codigo in codigos:
            if anonimizar_atestado(codigo):
                registrar_evento(
                    EVENTO_ATESTADO_ANONIMIZADO,
                    origem=ORIGEM_RETENCAO_AUTOMATICA,
                    atestado_codigo=codigo,
                    detalhe=f"retencao automatica: mais de {dias} dias desde a emissao",
                )
                total_anonimizados += 1
        return total_anonimizados
    except Exception:
        _LOGGER.error("Falha ao aplicar retencao automatica de atestados.", exc_info=True)
        return 0
