"""
documento_pdf.py — Gera o PDF do atestado diretamente no servidor, sem
depender do Canva: monta um HTML/CSS fiel ao layout oficial da AmorSaúde e
renderiza com weasyprint. Roda em segundo plano (thread daemon), nunca
bloqueia a emissão do atestado; se falhar, o dashboard oferece "Tentar
novamente" — mesmo contrato que a geração via Canva já tinha (ver
tabela `documentos_atestado` em src/database.py, inalterada).

O fluxo de chat (Claude + conector do Canva, documentado no CLAUDE.md seção
5.2) continua funcionando por fora deste módulo — ele não chama nada daqui,
só edita o template do Canva diretamente pela conversa.
"""

from __future__ import annotations

import base64
import html
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    # A importação do weasyprint carrega bibliotecas nativas do sistema
    # (Pango/GObject/fontconfig — ver Dockerfile). Se estiverem ausentes ou
    # mal configuradas, isso não pode derrubar o app inteiro na subida: só a
    # geração do PDF fica indisponível (mesmo contrato de degradação
    # graciosa que CANVA_CLIENT_ID/SECRET ausentes já tinham).
    from weasyprint import HTML as _WeasyprintHTML
except Exception:  # pragma: no cover - depende de bibliotecas de sistema
    _WeasyprintHTML = None

from src.audit import EVENTO_DOCUMENTO_FALHOU, EVENTO_DOCUMENTO_GERADO, registrar_evento
from src.crypto import criptografar_bytes, descriptografar_bytes
from src.database import (
    buscar_documento,
    iniciar_geracao_documento,
    marcar_documento_falhou,
    marcar_documento_pronto,
    remover_registro_documento,
)

_LOGGER = logging.getLogger("amorsaude.documento_pdf")

_DOCUMENTOS_DIR_NOME = "documentos"
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_LOGO_PATH = _ASSETS_DIR / "logo-amorsaude.png"

_CIDADE_UF_PADRAO = "Ribeirão Preto, - São Paulo"

_COR_PRIMARIA = "#5FC2D4"
_COR_TEXTO = "#262626"


def _logo_base64() -> str:
    """Lê o logo da AmorSaúde e devolve como data URI, ou string vazia se o arquivo não existir."""
    if not _LOGO_PATH.exists():
        return ""
    dados = _LOGO_PATH.read_bytes()
    return f"data:image/png;base64,{base64.b64encode(dados).decode('ascii')}"


def _formatar_data_br(data_iso: str) -> str:
    """Converte 'AAAA-MM-DD' para 'DD/MM/AAAA'. Se já não estiver nesse formato, devolve como veio."""
    try:
        return datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return str(data_iso or "")


def _diretorio_documentos() -> Path:
    base = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else Path(__file__).resolve().parent.parent / "data"
    caminho = base / _DOCUMENTOS_DIR_NOME
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def _montar_html(
    *,
    nome: str,
    cpf: str,
    data_inicio_br: str,
    dias: str,
    cid: str,
    data_emissao_br: str,
    nome_medico: str,
    crm: str,
    qr_base64: str,
) -> str:
    """
    Monta o HTML/CSS do atestado — layout fiel ao template oficial (fundo
    branco, faixa de título verde-água, corpo justificado, bloco de
    assinatura + QR alinhado à direita, rodapé verde-água com endereço e
    horário). Todo texto vindo do usuário (nome, cpf, cid, médico, crm) é
    escapado antes de entrar no HTML.
    """
    e = html.escape
    logo = _logo_base64()
    logo_tag = f'<img src="{logo}" alt="AmorSaúde">' if logo else '<span class="logo-texto">AmorSaúde</span>'
    logo_tag_pequena = f'<img class="logo-pequena" src="{logo}" alt="AmorSaúde">' if logo else ""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<style>
    @page {{ size: A4; margin: 0; }}
    * {{ box-sizing: border-box; }}
    body {{
        margin: 0;
        font-family: 'DejaVu Sans', Arial, sans-serif;
        color: {_COR_TEXTO};
    }}
    .cabecalho {{
        padding: 30px 44px 18px 44px;
        background: #FFFFFF;
    }}
    .cabecalho img {{ height: 44px; display: block; }}
    .cabecalho .logo-texto {{ font-size: 20pt; font-weight: 800; color: {_COR_PRIMARIA}; }}
    .linha-separadora {{
        border-bottom: 1px solid #D9D9D9;
        margin: 0 44px;
    }}
    .faixa-titulo {{
        background: {_COR_PRIMARIA};
        padding: 16px 0;
        text-align: center;
    }}
    .faixa-titulo h1 {{
        margin: 0;
        color: #FFFFFF;
        font-size: 20pt;
        font-weight: 800;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }}
    .corpo {{ padding: 40px 52px 0 52px; }}
    .paragrafo-principal {{
        font-size: 11pt;
        line-height: 1.7;
        text-align: justify;
    }}
    .espaco-grande {{ height: 64px; }}
    .observacao {{ font-size: 9.5pt; line-height: 1.6; }}
    .local-data {{
        display: flex;
        gap: 48px;
        font-size: 10.5pt;
    }}
    .bloco-assinatura {{
        display: flex;
        justify-content: flex-end;
        align-items: center;
        gap: 20px;
    }}
    .assinatura-texto {{
        text-align: right;
        font-size: 8pt;
        line-height: 1.55;
    }}
    .assinatura-texto .nome-medico {{ font-weight: 700; font-size: 9pt; }}
    .assinatura-texto img.logo-pequena {{ height: 15px; margin: 3px 0; }}
    .qr-code img {{ width: 120px; height: 120px; display: block; }}
    .icone-decorativo {{
        width: 26px;
        height: 26px;
        border-radius: 50%;
        background: {_COR_PRIMARIA};
        color: #FFFFFF;
        text-align: center;
        line-height: 26px;
        font-size: 15px;
        font-weight: 700;
        margin: 20px auto 0 auto;
    }}
    .rodape {{
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
        background: {_COR_PRIMARIA};
        color: #FFFFFF;
        padding: 16px 44px;
        display: flex;
        justify-content: space-between;
        font-size: 8pt;
        line-height: 1.6;
    }}
    .rodape .direita {{ text-align: right; }}
</style>
</head>
<body>
    <div class="cabecalho">{logo_tag}</div>
    <div class="linha-separadora"></div>
    <div class="faixa-titulo"><h1>Atestado Médico</h1></div>
    <div class="corpo">
        <p class="paragrafo-principal">
            Atesto para os devidos fins que {e(nome)} inscrito(a) sob CPF de N° {e(cpf)}
            o(a) paciente esteve sob meus cuidados no dia {e(data_inicio_br)}, necessitando de
            {e(dias)} Dia(s) de afastamento de todas suas atividades laborais por motivo de CID {e(cid)}
        </p>
        <div class="espaco-grande"></div>
        <p class="observacao">
            <strong>Observação:</strong> Este atestado foi emitido de forma digital, com validade
            legal, e assinado por meio de certificado digital. Para garantir sua autenticidade, o
            documento deve conter QR Code legível e não apresentar qualquer tipo de rasura ou alteração.
        </p>
        <div class="espaco-grande"></div>
        <div class="local-data">
            <span>{e(_CIDADE_UF_PADRAO)}</span>
            <span>{e(data_emissao_br)}</span>
        </div>
        <div class="espaco-grande"></div>
        <div class="bloco-assinatura">
            <div class="assinatura-texto">
                <div class="nome-medico">{e(nome_medico)}</div>
                <div>{e(crm)}</div>
                {logo_tag_pequena}
                <div>Verifique a autenticidade escaneando o Qr Code.</div>
                <div>Assinado Digitalmente pela plataforma App.AmorSaude.</div>
                <div><strong>Emitido em {e(data_emissao_br)}</strong></div>
            </div>
            <div class="qr-code"><img src="{qr_base64}" alt="QR Code"></div>
        </div>
        <div class="icone-decorativo">+</div>
    </div>
    <div class="rodape">
        <div class="esquerda">
            <div>Endereço:</div>
            <div>Rua Lafaiete, 1100, Centro, Ribeirão Preto-SP</div>
            <div>CEP: 14015-080</div>
        </div>
        <div class="direita">
            <div>Horário de funcionamento Segunda à sexta das 08:00 às 20:00 Sábado e</div>
            <div>Domingo das 08:00 às 18:00</div>
            <div>Telefone: (16) 3234-7750</div>
        </div>
    </div>
</body>
</html>"""


def _gerar_pdf_bytes(**kwargs) -> bytes:
    if _WeasyprintHTML is None:
        raise RuntimeError("weasyprint não pôde ser carregado neste servidor (bibliotecas de sistema ausentes).")
    html_str = _montar_html(**kwargs)
    return _WeasyprintHTML(string=html_str, base_url=str(_ASSETS_DIR)).write_pdf()


def _gerar_documento(
    codigo: str,
    *,
    nome: str,
    cpf: str,
    data_inicio_iso: str,
    dias,
    cid: str,
    data_emissao_iso: str,
    nome_medico: str,
    crm: str,
    qr_png: bytes,
    origem: str,
) -> None:
    """
    Roda a pipeline completa (montar HTML → renderizar PDF → salvar cifrado)
    e grava o resultado via `marcar_documento_pronto`/`marcar_documento_falhou`,
    registrando o evento correspondente na auditoria (só o código do
    atestado — nunca nome/CPF/CID). Nunca levanta exceção — é chamada dentro
    de uma thread em segundo plano por `disparar_geracao_documento()`.
    """
    try:
        qr_base64 = f"data:image/png;base64,{base64.b64encode(qr_png).decode('ascii')}"
        pdf_bytes = _gerar_pdf_bytes(
            nome=nome,
            cpf=cpf,
            data_inicio_br=_formatar_data_br(data_inicio_iso),
            dias=str(dias),
            cid=cid,
            data_emissao_br=_formatar_data_br(data_emissao_iso),
            nome_medico=nome_medico,
            crm=crm,
            qr_base64=qr_base64,
        )

        caminho = _diretorio_documentos() / f"{codigo}.pdf.enc"
        caminho.write_bytes(criptografar_bytes(pdf_bytes))
        marcar_documento_pronto(codigo, str(caminho))
        registrar_evento(EVENTO_DOCUMENTO_GERADO, atestado_codigo=codigo, origem=origem)
    except Exception:
        _LOGGER.error("Falha ao gerar PDF do atestado (codigo=%s)", codigo, exc_info=True)
        mensagem = "Falha inesperada ao gerar o documento."
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
    data_emissao_iso: str,
    nome_medico: str,
    crm: str,
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
            "dias": dias,
            "cid": cid,
            "data_emissao_iso": data_emissao_iso,
            "nome_medico": nome_medico,
            "crm": crm,
            "qr_png": qr_png,
            "origem": origem,
        },
        daemon=True,
        name=f"pdf-doc-{codigo[:8]}",
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
        _LOGGER.error("Falha ao remover documento do atestado codigo=%s", codigo, exc_info=True)


def ler_documento(codigo: str) -> Optional[bytes]:
    """Lê e decifra o PDF pronto de um atestado. Retorna None se não houver documento pronto ou o arquivo tiver sumido do disco."""
    registro = buscar_documento(codigo)
    if not registro or registro["status"] != "pronto" or not registro["caminho_arquivo"]:
        return None
    caminho = Path(registro["caminho_arquivo"])
    if not caminho.exists():
        return None
    return descriptografar_bytes(caminho.read_bytes())
