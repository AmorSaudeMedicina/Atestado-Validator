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
import os
from datetime import date, datetime, timedelta
import secrets as _secrets

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.audit import EVENTO_ATESTADO_EMITIDO, ORIGEM_API, registrar_evento
from src.documento_pdf import disparar_geracao_documento, ler_documento
from src.database import (
    buscar_atestado_por_codigo,
    buscar_medico_por_cidade,
    buscar_medico_por_token_hash,
    salvar_atestado,
)
from src.qr_generator import gerar_qr
from src.api_tokens import hash_token
from src.urls import url_qr_publica, url_verificacao

_FORMATO_DATA = "%Y-%m-%d"


class ErroValidacaoAtestado(ValueError):
    """Erro de validação dos dados de um atestado (mensagem já em português, pronta para exibir)."""


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


def _autenticar_integracao(request: Request) -> bool:
    """
    True se a requisição trouxe a CHAVE DE INTEGRAÇÃO correta (variável de
    ambiente `INTEGRACAO_API_KEY`), enviada como 'Authorization: Bearer
    <chave>' (ou X-API-Token). Diferente do token por médico, esta é uma
    única chave usada pela automação (n8n): ela não representa um médico —
    quem assina o atestado é resolvido pela cidade (ver
    `registrar_atestado_integracao`).

    Fail-closed: se a env não estiver definida, a integração fica DESLIGADA
    (sempre False). Comparação em tempo constante para não vazar a chave por
    diferença de tempo de resposta.
    """
    esperada = os.environ.get("INTEGRACAO_API_KEY", "").strip()
    if not esperada:
        return False
    apresentada = _extrair_token(request)
    if not apresentada:
        return False
    return _secrets.compare_digest(apresentada, esperada)


def _parse_data(valor: str, campo: str) -> date:
    try:
        return datetime.strptime(valor.strip(), _FORMATO_DATA).date()
    except (ValueError, AttributeError):
        raise ValueError(f"Campo '{campo}' deve estar no formato AAAA-MM-DD.")


def registrar_atestado_core(medico: dict, corpo: dict, origem: str, request: Request | None = None) -> dict:
    """
    Lógica central de registro de um atestado, compartilhada por TODOS os
    caminhos de entrada (API REST em `registrar_atestado` abaixo e o
    conector MCP em `src/mcp_server.py`) — garante que um atestado criado
    por qualquer um desses caminhos é gravado exatamente da mesma forma:
    mesmo `codigo`, aparece no dashboard do médico e pode ser revogado
    normalmente pelo fluxo já existente.

    `medico` já deve estar autenticado pelo chamador (dono do token).
    `corpo` é um dict com os mesmos campos aceitos pelo endpoint REST.
    `origem` identifica de onde veio a chamada (ver src.audit.ORIGEM_*) —
    grava na trilha de auditoria junto com o evento de emissão.

    `corpo["cpf"]` é OPCIONAL e NUNCA é salvo no atestado — se vier
    preenchido, dispara em segundo plano a geração do PDF (ver
    src/documento_pdf.py), que usa o CPF só para preencher o documento.

    `corpo["exibir_cid"]` é OPCIONAL (padrão False) — decide se o CID
    aparece em texto normal na página pública de verificação ou fica oculto
    atrás de "Protegido por sigilo médico" (ver seção 6 do CLAUDE.md).

    Levanta ErroValidacaoAtestado (mensagem em português) se os dados forem
    inválidos. Não grava nada no banco nesse caso.
    """
    if not isinstance(corpo, dict):
        raise ErroValidacaoAtestado("Os dados do atestado devem ser um objeto/dicionário.")

    nome_paciente = str(corpo.get("nome_paciente") or "").strip()
    cid = str(corpo.get("cid") or "").strip()
    # CPF é OPCIONAL e nunca é salvo no registro do atestado (decisão de
    # LGPD já documentada) — só existe, se informado, para preencher o
    # campo correspondente do PDF gerado localmente (ver disparo abaixo).
    cpf = str(corpo.get("cpf") or "").strip() or None
    exibir_cid = bool(corpo.get("exibir_cid"))
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
        raise ErroValidacaoAtestado("; ".join(erros))

    codigo = _secrets.token_urlsafe(32)

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
        exibir_cid=exibir_cid,
    )
    registrar_evento(
        EVENTO_ATESTADO_EMITIDO,
        ator_usuario=medico["usuario"],
        ator_perfil="medico",
        atestado_codigo=codigo,
        origem=origem,
    )

    # Geração do PDF: assíncrona (thread em segundo plano) e só dispara se
    # um CPF foi informado — nunca bloqueia nem falha a emissão do atestado
    # em si (ver src/documento_pdf.py).
    disparar_geracao_documento(
        codigo,
        nome=nome_paciente,
        cpf=cpf,
        data_inicio_iso=data_inicio_str or data_emissao_str or str(date.today()),
        dias=dias_afastamento,
        cid=cid.upper(),
        data_emissao_iso=data_emissao_str or str(date.today()),
        nome_medico=medico["nome"],
        crm=medico["crm"],
        qr_png=gerar_qr(url_verificacao(codigo, request)),
        origem=origem,
        endereco_rua=medico.get("endereco_rua"),
        endereco_cidade=medico.get("endereco_cidade"),
        endereco_estado=medico.get("endereco_estado"),
        endereco_cep=medico.get("endereco_cep"),
        endereco_telefone=medico.get("endereco_telefone"),
    )

    return {
        "codigo": codigo,
        "url_verificacao": url_verificacao(codigo, request),
        "qr_code_url": url_qr_publica(codigo, request),
        "nome_medico": medico["nome"],
        "crm": medico["crm"],
        "nome_paciente": nome_paciente,
        "cid": cid.upper(),
        "data_emissao": data_emissao_str,
        "data_inicio": data_inicio_str,
        "data_fim": data_fim_str,
        "dias_afastamento": dias_afastamento,
        "exibir_cid": exibir_cid,
    }


async def registrar_atestado(request: Request) -> Response:
    """
    POST /atestados

    Cabeçalho: Authorization: Bearer <token do médico>

    Corpo JSON:
        nome_paciente (str, obrigatório)
        cid (str, obrigatório)
        data_emissao (str "AAAA-MM-DD", obrigatório)
        dias_afastamento (int) — OU — data_inicio + data_fim (str "AAAA-MM-DD")
        cpf (str, opcional) — nunca é salvo; se informado, dispara a geração
            automática do PDF do atestado em segundo plano (disponível
            depois para download no dashboard do médico)
        exibir_cid (bool, opcional, padrão false) — se o CID aparece em
            texto normal na página pública de verificação, ou fica oculto
            atrás de "Protegido por sigilo médico"

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

    try:
        resultado = registrar_atestado_core(medico, corpo, ORIGEM_API, request)
    except ErroValidacaoAtestado as exc:
        return _erro(422, str(exc))
    except Exception:
        return _erro(500, "Erro interno ao salvar o atestado. Tente novamente.")

    return JSONResponse(resultado, status_code=201)


async def registrar_atestado_integracao(request: Request) -> Response:
    """
    POST /integracao/atestados

    Emissão pela AUTOMAÇÃO (n8n), autenticada por uma única CHAVE DE
    INTEGRAÇÃO (env `INTEGRACAO_API_KEY`, cabeçalho 'Authorization: Bearer
    <chave>') — e NÃO pelo token de um médico. O médico que assina é
    resolvido pelo backend a partir do campo 'cidade' do corpo, cruzando com
    o endereço cadastrado de cada médico no painel (`endereco_cidade`). Assim
    o mapa cidade→médico vive só no painel: cadastrou um médico com a cidade
    dele, a automação já passa a emitir por ele, sem tocar no n8n.

    Corpo JSON: os MESMOS campos de POST /atestados, mais:
        cidade (str, obrigatório) — escolhe o médico que assina o atestado.

    Respostas:
        201  atestado emitido (mesmo corpo de /atestados + 'medico_usuario')
        401  chave de integração ausente/errada (ou integração desligada)
        404  nenhum médico ativo cadastrado para a cidade informada
        422  'cidade' ausente ou dados do atestado inválidos
    """
    if not _autenticar_integracao(request):
        return _erro(401, "Chave de integração ausente ou inválida.")

    try:
        corpo = await request.json()
    except json.JSONDecodeError:
        return _erro(400, "Corpo da requisição deve ser um JSON válido.")
    if not isinstance(corpo, dict):
        return _erro(400, "Corpo da requisição deve ser um objeto JSON.")

    cidade = str(corpo.get("cidade") or "").strip()
    if not cidade:
        return _erro(422, "Campo 'cidade' é obrigatório para emitir via integração.")

    medicos = buscar_medico_por_cidade(cidade)
    if not medicos:
        return _erro(404, f"Nenhum médico ativo cadastrado para a cidade '{cidade}'.")
    # Se houver mais de um médico na mesma cidade, usa o mais antigo (a lista
    # já vem ordenada por id) — resultado determinístico. Refinar por UF/
    # unidade fica para quando houver esse caso de fato.
    medico = medicos[0]

    try:
        resultado = registrar_atestado_core(medico, corpo, ORIGEM_API, request)
    except ErroValidacaoAtestado as exc:
        return _erro(422, str(exc))
    except Exception:
        return _erro(500, "Erro interno ao salvar o atestado. Tente novamente.")

    # Informa qual médico assinou — útil para a automação registrar/depurar.
    resultado["medico_usuario"] = medico["usuario"]
    return JSONResponse(resultado, status_code=201)


_HEADERS_QR = {
    # Permite que qualquer servidor externo (Make, Zapier, etc.) busque
    # a imagem diretamente, inclusive via fetch de browser (sem bloqueio CORS).
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    # QR Code é imutável para um dado `codigo` — pode ser cacheado por CDNs e
    # browsers por 1 hora sem precisar revalidar no servidor.
    "Cache-Control": "public, max-age=3600, immutable",
}


async def obter_qr_code(request: Request) -> Response:
    """
    GET /api/atestados/{codigo}/qrcode.png
    OPTIONS /api/atestados/{codigo}/qrcode.png  (preflight CORS)

    Endpoint público (sem autenticação) — mesmo nível de acesso da página de
    verificação pública já existente (?codigo=...): o `codigo` em si é o
    segredo (32 bytes aleatórios, improvável de adivinhar), não o token do
    médico. Isso é o que permite que ferramentas externas baixem a imagem do
    QR Code diretamente por URL, sem login nem JavaScript.
    """
    # Responde ao preflight CORS enviado por browsers/ferramentas antes do GET.
    # Responde ao preflight CORS enviado por browsers/ferramentas antes do GET.
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=_HEADERS_QR)

    codigo = request.path_params["codigo"]
    atestado = buscar_atestado_por_codigo(codigo)
    if not atestado:
        return _erro(404, "Atestado não encontrado.")

    qr_bytes = gerar_qr(url_verificacao(codigo, request))
    return Response(
        content=qr_bytes,
        media_type="image/png",
        headers=_HEADERS_QR,
    )


async def obter_pdf(request: Request) -> Response:
    """
    GET /atestados/{codigo}/pdf

    Cabeçalho: Authorization: Bearer <token do médico>

    Devolve o PDF (já pronto e decifrado) de um atestado, para integrações
    (ex.: envio automático por WhatsApp após pagamento). Diferente da imagem
    do QR — que é pública porque não expõe dado pessoal —, o PDF carrega nome
    e CPF do paciente, então exige autenticação.

    Dois modos de autenticação são aceitos:
      - Token do médico (Bearer): só libera o PDF de um atestado emitido por
        ESSE médico.
      - Chave de integração (Bearer `INTEGRACAO_API_KEY`): usada pela
        automação (n8n) para baixar o PDF de qualquer atestado que ela mesma
        emitiu via /integracao/atestados — como o médico é escolhido pela
        cidade, a automação não tem o token dele para baixar depois.

    Respostas:
        200  application/pdf  (o arquivo)
        401  token/chave ausente ou inválido
        403  o atestado não pertence ao médico do token (modo token)
        404  atestado inexistente, ou PDF ainda não gerado / indisponível
    """
    via_integracao = _autenticar_integracao(request)
    medico = None
    if not via_integracao:
        medico, erro_auth = _autenticar_medico(request)
        if erro_auth is not None:
            return erro_auth

    codigo = request.path_params["codigo"]
    atestado = buscar_atestado_por_codigo(codigo)
    if not atestado:
        return _erro(404, "Atestado não encontrado.")
    if not via_integracao and atestado.get("crm") != medico.get("crm"):
        return _erro(403, "Este atestado não pertence ao médico do token.")

    pdf_bytes = ler_documento(codigo)
    if not pdf_bytes:
        # Ainda em geração (assíncrona) ou nunca gerado (ex.: emitido sem CPF).
        return _erro(404, "PDF ainda não está disponível (em geração ou não gerado).")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="atestado-{codigo}.pdf"'},
    )
