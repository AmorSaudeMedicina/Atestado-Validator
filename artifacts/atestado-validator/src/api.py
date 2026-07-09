"""
api.py — Endpoints HTTP programáticos para registro de atestados.

Estes endpoints rodam dentro do MESMO processo Streamlit (via rotas extras do
Starlette, ver server.py) e usam a MESMA camada de banco de dados (src/database.py)
e o MESMO gerador de QR Code (src/qr_generator.py) do formulário humano —
então um atestado criado pela API é, no banco, idêntico a um emitido pelo
formulário: mesmo `codigo`, aparece no dashboard do médico e pode ser
revogado normalmente pelo fluxo já existente.

Autenticação: cada chamada deve trazer um token de API (cabeçalho
`Authorization: Bearer <token>` ou `X-API-Token: <token>`) vinculado a um
médico específico e ativo. O médico do atestado é sempre o dono do token —
nunca um valor escolhido livremente por quem chama.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
import secrets as _secrets

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.database import buscar_atestado_por_codigo, buscar_medico_por_token_hash, salvar_atestado
from src.qr_generator import gerar_qr
from src.api_tokens import hash_token
from src.urls import url_qr_publica, url_verificacao

_FORMATO_DATA = "%Y-%m-%d"


def _erro(status: int, mensagem: str) -> JSONResponse:
    return JSONResponse({"erro": mensagem}, status_code=status)


def _extrair_token(request: Request) -> str | None:
    cabecalho_auth = request.headers.get("authorization", "")
    if cabecalho_auth.lower().startswith("bearer "):
        return cabecalho_auth[7:].strip() or None
    token_alternativo = request.headers.get("x-api-token", "").strip()
    return token_alternativo or None


def _autenticar_medico(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """Resolve o token da requisição para uma conta de médico ativa, ou retorna o erro a devolver."""
    token = _extrair_token(request)
    if not token:
        return None, _erro(401, "Token de API ausente. Envie 'Authorization: Bearer <token>'.")

    medico = buscar_medico_por_token_hash(hash_token(token))
    if not medico:
        return None, _erro(401, "Token de API inválido, revogado ou de médico inativo.")

    return medico, None


def _parse_data(valor: str, campo: str) -> date:
    try:
        return datetime.strptime(valor.strip(), _FORMATO_DATA).date()
    except (ValueError, AttributeError):
        raise ValueError(f"Campo '{campo}' deve estar no formato AAAA-MM-DD.")


async def registrar_atestado(request: Request) -> Response:
    """
    POST /api/atestados

    Cabeçalho: Authorization: Bearer <token do médico>

    Corpo JSON:
        nome_paciente (str, obrigatório)
        cid (str, obrigatório)
        data_emissao (str "AAAA-MM-DD", obrigatório)
        dias_afastamento (int) — OU — data_inicio + data_fim (str "AAAA-MM-DD")

    Resposta 201 JSON:
        codigo, url_verificacao, qr_code_url, nome_medico, crm
    """
    medico, erro_auth = _autenticar_medico(request)
    if erro_auth is not None:
        return erro_auth

    try:
        corpo = await request.json()
    except json.JSONDecodeError:
        return _erro(400, "Corpo da requisição deve ser um JSON válido.")

    if not isinstance(corpo, dict):
        return _erro(400, "Corpo da requisição deve ser um objeto JSON.")

    nome_paciente = str(corpo.get("nome_paciente") or "").strip()
    cid = str(corpo.get("cid") or "").strip()
    data_emissao_bruta = corpo.get("data_emissao")
    dias_afastamento_bruto = corpo.get("dias_afastamento")
    data_inicio_bruta = corpo.get("data_inicio")
    data_fim_bruta = corpo.get("data_fim")

    erros: list[str] = []
    if not nome_paciente:
        erros.append("Campo 'nome_paciente' é obrigatório.")
    if not cid:
        erros.append("Campo 'cid' é obrigatório.")
    if not data_emissao_bruta:
        erros.append("Campo 'data_emissao' é obrigatório (formato AAAA-MM-DD).")

    data_emissao_str: str | None = None
    if data_emissao_bruta:
        try:
            data_emissao_str = str(_parse_data(str(data_emissao_bruta), "data_emissao"))
        except ValueError as exc:
            erros.append(str(exc))

    usa_dias = dias_afastamento_bruto is not None
    usa_periodo = data_inicio_bruta is not None or data_fim_bruta is not None

    dias_afastamento: int | None = None
    data_inicio_str: str | None = None
    data_fim_str: str | None = None

    if usa_dias and usa_periodo:
        erros.append("Informe 'dias_afastamento' OU 'data_inicio'+'data_fim', não os dois.")
    elif usa_dias:
        try:
            dias_afastamento = int(dias_afastamento_bruto)
            if dias_afastamento < 1:
                erros.append("Campo 'dias_afastamento' deve ser maior ou igual a 1.")
        except (TypeError, ValueError):
            erros.append("Campo 'dias_afastamento' deve ser um número inteiro.")
    elif usa_periodo:
        if not data_inicio_bruta or not data_fim_bruta:
            erros.append("Informe 'data_inicio' e 'data_fim' juntos.")
        else:
            try:
                data_inicio = _parse_data(str(data_inicio_bruta), "data_inicio")
                data_fim = _parse_data(str(data_fim_bruta), "data_fim")
                if data_fim < data_inicio:
                    erros.append("Campo 'data_fim' não pode ser anterior a 'data_inicio'.")
                else:
                    data_inicio_str = str(data_inicio)
                    data_fim_str = str(data_fim)
                    dias_afastamento = (data_fim - data_inicio).days + 1
            except ValueError as exc:
                erros.append(str(exc))
    else:
        erros.append(
            "Informe o período de afastamento: 'dias_afastamento' ou 'data_inicio'+'data_fim'."
        )

    if erros:
        return _erro(422, "; ".join(erros))

    codigo = _secrets.token_urlsafe(32)

    try:
        salvar_atestado(
            codigo=codigo,
            nome_medico=medico["nome"],
            crm=medico["crm"],
            nome_paciente=nome_paciente,
            cid=cid.upper(),
            data_emissao=data_emissao_str or str(date.today()),
            data_inicio=data_inicio_str,
            data_fim=data_fim_str,
            dias_afastamento=dias_afastamento,
        )
    except Exception:
        return _erro(500, "Erro interno ao salvar o atestado. Tente novamente.")

    return JSONResponse(
        {
            "codigo": codigo,
            "url_verificacao": url_verificacao(codigo),
            "qr_code_url": url_qr_publica(codigo),
            "nome_medico": medico["nome"],
            "crm": medico["crm"],
            "nome_paciente": nome_paciente,
            "cid": cid.upper(),
            "data_emissao": data_emissao_str,
            "data_inicio": data_inicio_str,
            "data_fim": data_fim_str,
            "dias_afastamento": dias_afastamento,
        },
        status_code=201,
    )


async def obter_qr_code(request: Request) -> Response:
    """
    GET /api/atestados/{codigo}/qrcode.png

    Endpoint público (sem autenticação) — mesmo nível de acesso da página de
    verificação pública já existente (?codigo=...): o `codigo` em si é o
    segredo (32 bytes aleatórios, improvável de adivinhar), não o token do
    médico. Isso é o que permite que ferramentas externas (ex.: Canva) baixem
    a imagem do QR Code diretamente por URL.
    """
    codigo = request.path_params["codigo"]
    atestado = buscar_atestado_por_codigo(codigo)
    if not atestado:
        return _erro(404, "Atestado não encontrado.")

    qr_bytes = gerar_qr(url_verificacao(codigo))
    return Response(
        content=qr_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )
