"""
canva_client.py — Integração direta com a API do Canva (Connect API), sem
Claude/IA no meio: o próprio servidor autentica uma vez (OAuth 2.0 + PKCE),
guarda o token cifrado, e a partir daí gera o PDF do atestado sozinho a
cada emissão.

Por que "Autofill" e não "duplicar + editar" (como o fluxo de chat faz):
a API pública do Canva não expõe um endpoint genérico de "duplicar design"
nem de "editar o texto/imagem de um elemento específico" — a única forma
oficial de programaticamente pegar um design/template e gerar uma CÓPIA com
dados diferentes é a Autofill API (`POST /v1/autofills`, `type:
"create_from_design"`), que já cria um design novo (nunca toca no
original) preenchendo campos previamente marcados nele. Por isso o
pré-requisito abaixo é obrigatório.

PRÉ-REQUISITOS (fora do alcance deste código — feitos manualmente):
1. Uma Integration criada em canva.com/developers (conta do Canva em uso —
   HOJE é uma conta de TESTE; ver aviso no CLAUDE.md sobre reautorizar ao
   trocar para produção), com escopos design:content (read+write),
   design:meta (read) e asset (read+write), e a redirect_uri configurada
   apontando para {domínio do app}/admin/canva/callback.
2. O design "TEMPLATE PARA CLAUDE" precisa ter, no editor do Canva, cada
   elemento dinâmico marcado como campo de autofill (nome, CPF, data de
   início, dias, CID como texto; o elemento do QR como imagem). Os nomes
   exatos usados são configuráveis via CANVA_CAMPO_* (ver `_CAMPOS` abaixo)
   — precisam bater com os nomes marcados no template.
3. Um administrador autoriza o servidor uma única vez em
   /admin/canva/conectar (ver src/server.py) — o token fica guardado
   cifrado no banco (nunca em texto puro, nunca no código/GitHub) e é
   renovado automaticamente a partir do refresh token.

Falhas nunca derrubam a emissão do atestado: `disparar_geracao_documento()`
roda em uma thread separada e qualquer erro (Canva fora do ar, token
expirado sem conseguir renovar, campo de autofill não encontrado etc.) só
marca o documento como 'falhou' — o atestado e o QR continuam válidos
normalmente, e o dashboard oferece "Tentar novamente".
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

from src.audit import EVENTO_DOCUMENTO_FALHOU, EVENTO_DOCUMENTO_GERADO, registrar_evento
from src.crypto import criptografar, criptografar_bytes, descriptografar, descriptografar_bytes
from src.database import (
    buscar_canva_oauth_token,
    buscar_documento,
    iniciar_geracao_documento,
    marcar_documento_falhou,
    marcar_documento_pronto,
    remover_registro_documento,
    salvar_canva_oauth_token,
)

_LOGGER = logging.getLogger("amorsaude.canva")

_AUTH_BASE = "https://www.canva.com/api/oauth"
_API_BASE = "https://api.canva.com/rest/v1"
# A tela de autorização (navegador) fica em www.canva.com, mas a troca do
# código/refresh por token é uma chamada servidor-a-servidor na API REST
# (domínio diferente!) — não são simétricos, por isso uma constante própria.
_TOKEN_URL = f"{_API_BASE}/oauth/token"

_CLIENT_ID = os.environ.get("CANVA_CLIENT_ID", "").strip()
_CLIENT_SECRET = os.environ.get("CANVA_CLIENT_SECRET", "").strip()
_TEMPLATE_DESIGN_ID = os.environ.get("CANVA_TEMPLATE_DESIGN_ID", "DAHO7Z4z7P8").strip()

# Escopos mínimos necessários: ler/gravar conteúdo de design (autofill e
# export leem conteúdo), metadados de design, e ler/gravar assets (upload
# do QR Code).
_SCOPES = "design:content:write design:content:read design:meta:read asset:write asset:read"

# Nomes dos campos de autofill marcados no template — configuráveis via env
# var para não exigir mudança de código se os nomes reais no Canva forem
# diferentes destes valores padrão.
_CAMPOS = {
    "nome": os.environ.get("CANVA_CAMPO_NOME", "nome"),
    "cpf": os.environ.get("CANVA_CAMPO_CPF", "cpf"),
    "data_inicio": os.environ.get("CANVA_CAMPO_DATA_INICIO", "data_inicio"),
    "dias": os.environ.get("CANVA_CAMPO_DIAS", "dias"),
    "cid": os.environ.get("CANVA_CAMPO_CID", "cid"),
    "qr": os.environ.get("CANVA_CAMPO_QR", "qr_code"),
}

_DOCUMENTOS_DIR_NOME = "documentos"
_FORMATO_TIMESTAMP = "%Y-%m-%d %H:%M:%S"


class CanvaNaoConfigurado(RuntimeError):
    """CANVA_CLIENT_ID/CANVA_CLIENT_SECRET ausentes — a integração não foi configurada no ambiente."""


class CanvaNaoConectado(RuntimeError):
    """Nenhum administrador autorizou o servidor no Canva ainda (ou a conexão foi removida)."""


class ErroCanva(RuntimeError):
    """Erro de comunicação com a API do Canva — mensagem curta, sem dado sensível, própria para log/exibição."""


def configurado() -> bool:
    """True se CANVA_CLIENT_ID e CANVA_CLIENT_SECRET estão definidos no ambiente."""
    return bool(_CLIENT_ID and _CLIENT_SECRET)


def conectado() -> bool:
    """True se um administrador já autorizou o servidor (há um token guardado)."""
    return buscar_canva_oauth_token() is not None


# ---------------------------------------------------------------------------
# OAuth 2.0 + PKCE — autorização inicial (uma vez, por um admin) e renovação
# automática do token (a cada chamada, se necessário).
# ---------------------------------------------------------------------------

def gerar_par_pkce() -> tuple[str, str]:
    """Gera (code_verifier, code_challenge) — S256, como exigido pelo Canva."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def url_autorizacao(redirect_uri: str, state: str, code_challenge: str) -> str:
    """Monta a URL para onde o navegador do admin deve ser redirecionado para autorizar o app no Canva."""
    if not configurado():
        raise CanvaNaoConfigurado(
            "CANVA_CLIENT_ID e/ou CANVA_CLIENT_SECRET não configurados no ambiente."
        )
    params = {
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": _SCOPES,
        "response_type": "code",
        "client_id": _CLIENT_ID,
        "state": state,
        "redirect_uri": redirect_uri,
    }
    return f"{_AUTH_BASE}/authorize?{urlencode(params)}"


def _expira_em_str(expires_in_segundos: int) -> str:
    # Renova um pouco antes do vencimento real (60s de folga) para nunca usar
    # um token que expira no meio de uma chamada.
    momento = datetime.now() + timedelta(seconds=max(int(expires_in_segundos) - 60, 0))
    return momento.strftime(_FORMATO_TIMESTAMP)


def _gravar_tokens(dados: dict, conectado_por: Optional[str] = None) -> None:
    salvar_canva_oauth_token(
        access_token_cifrado=criptografar(dados["access_token"]),
        refresh_token_cifrado=criptografar(dados["refresh_token"]),
        expira_em_iso=_expira_em_str(dados["expires_in"]),
        conectado_por=conectado_por,
    )


def trocar_codigo_por_token(code: str, code_verifier: str, redirect_uri: str, conectado_por: str) -> None:
    """Troca o código de autorização (recebido em /admin/canva/callback) pelo primeiro par access+refresh token."""
    resposta = requests.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        },
        auth=(_CLIENT_ID, _CLIENT_SECRET),
        timeout=15,
    )
    if resposta.status_code != 200:
        raise ErroCanva(
            f"Canva recusou a troca do código de autorização (status {resposta.status_code}): "
            f"{resposta.text[:500]} | redirect_uri enviado: {redirect_uri}"
        )
    _gravar_tokens(resposta.json(), conectado_por=conectado_por)


def _obter_access_token_valido() -> str:
    """
    Retorna um access token do Canva pronto para uso, renovando via refresh
    token se estiver perto de expirar. Levanta CanvaNaoConectado /
    CanvaNaoConfigurado / ErroCanva conforme o caso — sempre com mensagens
    curtas e sem dado sensível.
    """
    if not configurado():
        raise CanvaNaoConfigurado("CANVA_CLIENT_ID/CANVA_CLIENT_SECRET não configurados.")

    registro = buscar_canva_oauth_token()
    if not registro:
        raise CanvaNaoConectado(
            "Nenhuma conta do Canva conectada. Peça a um administrador para acessar /admin/canva/conectar."
        )

    expira_em = datetime.strptime(registro["expira_em"], _FORMATO_TIMESTAMP)
    if datetime.now() < expira_em:
        return descriptografar(registro["access_token_cifrado"])

    # Renova — o refresh token do Canva é de uso único: a resposta sempre
    # traz um refresh token NOVO, que substitui o anterior no banco.
    refresh_token = descriptografar(registro["refresh_token_cifrado"])
    resposta = requests.post(
        _TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(_CLIENT_ID, _CLIENT_SECRET),
        timeout=15,
    )
    if resposta.status_code != 200:
        raise ErroCanva(
            f"Falha ao renovar o token do Canva (status {resposta.status_code}): {resposta.text[:500]}. "
            "Pode ser necessário reconectar em /admin/canva/conectar."
        )
    dados = resposta.json()
    _gravar_tokens(dados, conectado_por=registro.get("conectado_por"))
    return dados["access_token"]


# ---------------------------------------------------------------------------
# Pipeline: upload do QR → autofill do template → export em PDF.
# ---------------------------------------------------------------------------

def _cabecalho_auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _aguardar_job(url: str, token: str, *, tentativas_max: int, intervalo_segundos: float = 1.5) -> dict:
    """Faz polling de um job assíncrono do Canva até status success/failed, ou estoura tentativas_max."""
    for _ in range(tentativas_max):
        resposta = requests.get(url, headers=_cabecalho_auth(token), timeout=15)
        if resposta.status_code != 200:
            raise ErroCanva(f"Falha ao consultar job do Canva (status {resposta.status_code}).")
        job = resposta.json().get("job", {})
        status = job.get("status")
        if status == "success":
            return job
        if status == "failed":
            detalhe = (job.get("error") or {}).get("message", "erro desconhecido")
            raise ErroCanva(f"Job do Canva falhou: {detalhe}")
        time.sleep(intervalo_segundos)
    raise ErroCanva("Job do Canva não terminou a tempo (timeout).")


def _upload_asset_qr(token: str, dados_png: bytes, nome_arquivo: str) -> str:
    metadata = json.dumps({"name_base64": base64.b64encode(nome_arquivo.encode("utf-8")).decode("ascii")})
    resposta = requests.post(
        f"{_API_BASE}/asset-uploads",
        headers={
            **_cabecalho_auth(token),
            "Content-Type": "application/octet-stream",
            "Asset-Upload-Metadata": metadata,
        },
        data=dados_png,
        timeout=30,
    )
    if resposta.status_code not in (200, 202):
        raise ErroCanva(f"Falha ao enviar o QR Code para o Canva (status {resposta.status_code}).")
    job_id = resposta.json()["job"]["id"]
    job = _aguardar_job(f"{_API_BASE}/asset-uploads/{job_id}", token, tentativas_max=30)
    return job["asset"]["id"]


def verificar_campos_template() -> dict:
    """
    Consulta a API "Get design dataset" do Canva para o template configurado
    (`CANVA_TEMPLATE_DESIGN_ID`) e devolve os campos de autofill que ele
    REALMENTE tem (nome + tipo), direto da fonte — sem depender de conferência
    manual no editor. Útil para diagnosticar, logo após conectar, se os nomes
    em `_CAMPOS` batem com o que foi marcado no template.

    Não é chamada automaticamente por nada do fluxo normal — é uma ferramenta
    de diagnóstico (ex.: rodada manualmente por um script, ou futuramente por
    uma tela de admin). Levanta as mesmas exceções de `_obter_access_token_valido()`
    se o Canva não estiver configurado/conectado, e ErroCanva se a chamada falhar.
    """
    token = _obter_access_token_valido()
    resposta = requests.get(
        f"{_API_BASE}/designs/{_TEMPLATE_DESIGN_ID}/dataset",
        headers=_cabecalho_auth(token),
        timeout=15,
    )
    if resposta.status_code != 200:
        raise ErroCanva(
            f"Falha ao consultar os campos do template (status {resposta.status_code}): {resposta.text[:300]}"
        )
    dataset = resposta.json().get("dataset", {})
    return {
        nome_campo: info.get("type")
        for nome_campo, info in dataset.items()
    }


def _autofill_template(token: str, *, nome: str, cpf: str, data_inicio_br: str, dias: str, cid: str, asset_id_qr: str) -> str:
    corpo = {
        "type": "create_from_design",
        "design_id": _TEMPLATE_DESIGN_ID,
        "data": {
            _CAMPOS["nome"]: {"type": "text", "text": nome},
            _CAMPOS["cpf"]: {"type": "text", "text": cpf},
            _CAMPOS["data_inicio"]: {"type": "text", "text": data_inicio_br},
            _CAMPOS["dias"]: {"type": "text", "text": dias},
            _CAMPOS["cid"]: {"type": "text", "text": cid},
            _CAMPOS["qr"]: {"type": "image", "asset_id": asset_id_qr},
        },
    }
    resposta = requests.post(
        f"{_API_BASE}/autofills",
        headers={**_cabecalho_auth(token), "Content-Type": "application/json"},
        json=corpo,
        timeout=30,
    )
    if resposta.status_code not in (200, 202):
        raise ErroCanva(
            f"Falha ao preencher o template no Canva (status {resposta.status_code}). "
            "Confira se os campos de autofill do template batem com CANVA_CAMPO_*."
        )
    job_id = resposta.json()["job"]["id"]
    job = _aguardar_job(f"{_API_BASE}/autofills/{job_id}", token, tentativas_max=40)
    return job["result"]["design"]["id"]


def _exportar_pdf(token: str, design_id: str) -> bytes:
    corpo = {"design_id": design_id, "format": {"type": "pdf"}}
    resposta = requests.post(
        f"{_API_BASE}/exports",
        headers={**_cabecalho_auth(token), "Content-Type": "application/json"},
        json=corpo,
        timeout=30,
    )
    if resposta.status_code not in (200, 202):
        raise ErroCanva(f"Falha ao exportar o PDF no Canva (status {resposta.status_code}).")
    job_id = resposta.json()["job"]["id"]
    job = _aguardar_job(f"{_API_BASE}/exports/{job_id}", token, tentativas_max=60)
    urls = job.get("urls") or []
    if not urls:
        raise ErroCanva("Canva não retornou um link de download para o PDF exportado.")
    resposta_pdf = requests.get(urls[0], timeout=60)
    if resposta_pdf.status_code != 200:
        raise ErroCanva(f"Falha ao baixar o PDF exportado (status {resposta_pdf.status_code}).")
    return resposta_pdf.content


def _formatar_data_br(data_iso: str) -> str:
    """Converte 'AAAA-MM-DD' para 'DD/MM/AAAA'. Se já não estiver nesse formato, devolve como veio."""
    try:
        return datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return data_iso


def _diretorio_documentos() -> Path:
    base = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else Path(__file__).resolve().parent.parent / "data"
    caminho = base / _DOCUMENTOS_DIR_NOME
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def _gerar_documento(
    codigo: str, *, nome: str, cpf: str, data_inicio_iso: str, dias: str, cid: str, qr_png: bytes, origem: str
) -> None:
    """
    Roda a pipeline completa (upload → autofill → export → salvar cifrado)
    e grava o resultado via `marcar_documento_pronto`/`marcar_documento_falhou`,
    registrando o evento correspondente na auditoria (só o código do
    atestado — nunca nome/CPF/CID). Nunca levanta exceção — é chamada
    dentro de uma thread em segundo plano por `disparar_geracao_documento()`.
    """
    try:
        token = _obter_access_token_valido()
        asset_id = _upload_asset_qr(token, qr_png, f"qr-{codigo}.png")
        novo_design_id = _autofill_template(
            token,
            nome=nome,
            cpf=cpf,
            data_inicio_br=_formatar_data_br(data_inicio_iso),
            dias=dias,
            cid=cid,
            asset_id_qr=asset_id,
        )
        pdf_bytes = _exportar_pdf(token, novo_design_id)

        caminho = _diretorio_documentos() / f"{codigo}.pdf.enc"
        caminho.write_bytes(criptografar_bytes(pdf_bytes))
        marcar_documento_pronto(codigo, str(caminho))
        registrar_evento(EVENTO_DOCUMENTO_GERADO, atestado_codigo=codigo, origem=origem)
    except (CanvaNaoConfigurado, CanvaNaoConectado) as exc:
        _LOGGER.warning("Documento Canva nao gerado (codigo=%s): %s", codigo, exc)
        marcar_documento_falhou(codigo, str(exc))
        registrar_evento(EVENTO_DOCUMENTO_FALHOU, atestado_codigo=codigo, origem=origem, detalhe=str(exc)[:200])
    except Exception as exc:
        _LOGGER.error("Falha ao gerar documento Canva (codigo=%s)", codigo, exc_info=True)
        mensagem = str(exc) if isinstance(exc, ErroCanva) else "Falha inesperada ao gerar o documento."
        marcar_documento_falhou(codigo, mensagem)
        registrar_evento(EVENTO_DOCUMENTO_FALHOU, atestado_codigo=codigo, origem=origem, detalhe=mensagem[:200])


def disparar_geracao_documento(
    codigo: str,
    *,
    nome: str,
    cpf: Optional[str],
    data_inicio_iso: str,
    dias,
    cid: str,
    qr_png: bytes,
    origem: str,
) -> None:
    """
    Dispara a geração do PDF em segundo plano (thread daemon) — nunca
    bloqueia a emissão do atestado (formulário, API ou MCP).

    Se `cpf` vier vazio/None, não faz nada: o CPF só existe para preencher o
    documento (nunca é salvo no registro do atestado — decisão de LGPD já
    documentada), e sem ele não há como preencher o template.
    """
    if not cpf or not cpf.strip():
        return
    iniciar_geracao_documento(codigo)
    thread = threading.Thread(
        target=_gerar_documento,
        kwargs={
            "codigo": codigo,
            "nome": nome,
            "cpf": cpf.strip(),
            "data_inicio_iso": data_inicio_iso,
            "dias": str(dias),
            "cid": cid,
            "qr_png": qr_png,
            "origem": origem,
        },
        daemon=True,
        name=f"canva-doc-{codigo[:8]}",
    )
    thread.start()


def excluir_documento_gerado(codigo: str) -> None:
    """
    Apaga o PDF gerado (se houver) do disco e o registro correspondente.

    Chamada por src/retencao.py quando um atestado é anonimizado ou
    excluído — sem isto, anonimizar/excluir o registro no banco não
    adiantaria nada para os dados de nome/CPF que já estivessem gravados
    dentro de um PDF exportado anteriormente. Nunca levanta exceção: se o
    arquivo já não existir, ou a remoção falhar, só registra no log — não
    deve impedir a anonimização/exclusão do atestado em si.
    """
    try:
        caminho_str = remover_registro_documento(codigo)
        if caminho_str:
            caminho = Path(caminho_str)
            if caminho.exists():
                caminho.unlink()
    except Exception:
        _LOGGER.error("Falha ao remover documento Canva do atestado codigo=%s", codigo, exc_info=True)


def ler_documento(codigo: str) -> Optional[bytes]:
    """Lê e decifra o PDF pronto de um atestado. Retorna None se não houver documento pronto ou o arquivo tiver sumido do disco."""
    registro = buscar_documento(codigo)
    if not registro or registro["status"] != "pronto" or not registro["caminho_arquivo"]:
        return None
    caminho = Path(registro["caminho_arquivo"])
    if not caminho.exists():
        return None
    return descriptografar_bytes(caminho.read_bytes())
