"""
mcp_server.py — Conector MCP (Model Context Protocol) para registro de atestados.

Expõe a MESMA lógica de registro de `src/api.py` (registrar_atestado_core)
como uma ferramenta MCP ("registrar_atestado"), para que a Claude (ou outro
cliente MCP) consiga chamá-la diretamente numa conversa — sem passar por um
formulário nem por um agente intermediário. Um atestado criado pelo conector
é, no banco, idêntico a um emitido pelo formulário ou pela API REST: mesmo
`codigo`, aparece no dashboard do médico e pode ser revogado normalmente.

Transporte: "Streamable HTTP" (uma única rota HTTP aceitando mensagens
JSON-RPC 2.0 via POST). Este servidor é propositalmente simples/sem estado:
não usa streaming (SSE) nem sessões — cada requisição POST é respondida com
um único JSON, o que é uma forma válida do transporte para servidores que não
precisam enviar mensagens assíncronas ao cliente.

Autenticação: o token de API do médico (o MESMO token usado na API REST) fica
embutido na própria URL do conector (`/mcp/{token}`), pois a maioria dos
clientes MCP remotos (incluindo a Claude) não oferece um campo simples para
enviar um cabeçalho Bearer fixo por conector — uma URL única por médico é a
forma mais simples de garantir que cada chamada seja atribuída ao médico
correto. O token em si continua sendo o mesmo segredo de alta entropia já
usado pela API REST; revogar o token na tela "Token de API" invalida também
o conector MCP imediatamente.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.api import ErroValidacaoAtestado, registrar_atestado_core
from src.api_tokens import hash_token
from src.database import buscar_medico_por_token_hash

_PROTOCOLO_PADRAO = "2025-06-18"

_FERRAMENTA_REGISTRAR_ATESTADO = {
    "name": "registrar_atestado",
    "description": (
        "Registra um novo atestado médico no sistema AmorSaúde em nome do médico "
        "dono deste conector, e retorna o código único do atestado, a URL pública "
        "de verificação e o link direto da imagem do QR Code (PNG). O atestado "
        "criado fica idêntico a um emitido pelo formulário: aparece no dashboard "
        "do médico e pode ser revogado normalmente."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "nome_paciente": {
                "type": "string",
                "description": "Nome completo do paciente.",
            },
            "cid": {
                "type": "string",
                "description": "Código CID do diagnóstico (ex.: 'J06.9').",
            },
            "data_emissao": {
                "type": "string",
                "description": "Data de emissão do atestado, formato AAAA-MM-DD.",
            },
            "dias_afastamento": {
                "type": "integer",
                "description": (
                    "Quantidade de dias de afastamento. Use este campo OU "
                    "'data_inicio' + 'data_fim' — nunca os dois."
                ),
            },
            "data_inicio": {
                "type": "string",
                "description": "Data de início do afastamento, formato AAAA-MM-DD (usar junto com data_fim).",
            },
            "data_fim": {
                "type": "string",
                "description": "Data de fim do afastamento, formato AAAA-MM-DD (usar junto com data_inicio).",
            },
        },
        "required": ["nome_paciente", "cid", "data_emissao"],
    },
}


def _resultado_jsonrpc(id_: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _erro_jsonrpc(id_: Any, code: int, mensagem: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": mensagem}}


async def _processar_mensagem(msg: Any, medico: dict) -> dict | None:
    """
    Processa uma única mensagem JSON-RPC e devolve a resposta (dict) a ser
    serializada, ou None se a mensagem for uma notificação (sem `id`), que
    por definição do protocolo JSON-RPC não recebe resposta.
    """
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        id_bruto = msg.get("id") if isinstance(msg, dict) else None
        return _erro_jsonrpc(id_bruto, -32600, "Requisição JSON-RPC inválida.")

    metodo = msg.get("method")
    eh_notificacao = "id" not in msg
    id_ = msg.get("id")
    params = msg.get("params") or {}

    if metodo == "initialize":
        protocolo_cliente = params.get("protocolVersion") or _PROTOCOLO_PADRAO
        resultado = {
            "protocolVersion": protocolo_cliente,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "amorsaude-atestados",
                "title": "Validador de Atestados AmorSaúde",
                "version": "1.0.0",
            },
        }
    elif metodo in ("notifications/initialized", "notifications/cancelled"):
        return None
    elif metodo == "ping":
        resultado = {}
    elif metodo == "tools/list":
        resultado = {"tools": [_FERRAMENTA_REGISTRAR_ATESTADO]}
    elif metodo == "tools/call":
        nome_ferramenta = params.get("name")
        argumentos = params.get("arguments") or {}
        if nome_ferramenta != "registrar_atestado":
            if eh_notificacao:
                return None
            return _erro_jsonrpc(id_, -32602, f"Ferramenta desconhecida: '{nome_ferramenta}'.")
        try:
            dados = registrar_atestado_core(medico, argumentos)
            texto = json.dumps(dados, ensure_ascii=False, indent=2)
            resultado = {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Atestado registrado com sucesso.\n\n" + texto
                        ),
                    }
                ],
                "isError": False,
            }
        except ErroValidacaoAtestado as exc:
            resultado = {
                "content": [{"type": "text", "text": f"Erro de validação: {exc}"}],
                "isError": True,
            }
        except Exception:
            resultado = {
                "content": [
                    {
                        "type": "text",
                        "text": "Erro interno ao registrar o atestado. Tente novamente.",
                    }
                ],
                "isError": True,
            }
    else:
        if eh_notificacao:
            return None
        return _erro_jsonrpc(id_, -32601, f"Método não suportado: '{metodo}'.")

    if eh_notificacao:
        return None
    return _resultado_jsonrpc(id_, resultado)


async def mcp_endpoint(request: Request) -> Response:
    """
    POST /mcp/{token} — endpoint único do conector MCP (transporte Streamable HTTP,
    sem streaming/sessão: cada requisição recebe uma resposta JSON direta).

    O token na URL identifica o médico dono do conector — o mesmo usado na API
    REST. Token ausente/inválido/de médico inativo é recusado antes de
    qualquer processamento JSON-RPC, e nenhuma mensagem chega a ser lida.
    """
    token = request.path_params.get("token", "")
    medico = buscar_medico_por_token_hash(hash_token(token)) if token else None
    if not medico:
        return JSONResponse(
            {"erro": "Token de API inválido, revogado ou de médico inativo."},
            status_code=401,
        )

    if request.method != "POST":
        # O transporte Streamable HTTP permite ao servidor recusar GET quando
        # não há suporte a streaming assíncrono (SSE) — este conector não
        # envia mensagens espontâneas ao cliente, então cada chamada é só
        # requisição/resposta via POST.
        return Response(status_code=405)

    try:
        corpo = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_erro_jsonrpc(None, -32700, "Erro ao interpretar o JSON enviado."))

    if isinstance(corpo, list):
        respostas = []
        for msg in corpo:
            resposta = await _processar_mensagem(msg, medico)
            if resposta is not None:
                respostas.append(resposta)
        if not respostas:
            return Response(status_code=202)
        return JSONResponse(respostas)

    resposta = await _processar_mensagem(corpo, medico)
    if resposta is None:
        return Response(status_code=202)
    return JSONResponse(resposta)
