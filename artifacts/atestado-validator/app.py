"""
Validador de Atestados — app principal Streamlit.

Roteamento de telas:
  ?codigo=XXX  → Página pública de verificação (sem login)
  (sem código) → Login → Dashboard do médico

Identidade visual: AmorSaúde (verde-água + coral). Este arquivo trata apenas
de apresentação/estrutura — a lógica de banco de dados, autenticação, QR Code
e validação permanece intacta em src/.
"""

import base64
import csv
import hashlib
import html
import io
import os
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from src.audit import (
    EVENTO_ATESTADO_EMITIDO,
    EVENTO_ATESTADO_REVOGADO,
    EVENTO_MEDICO_ATIVADO,
    EVENTO_MEDICO_CRIADO,
    EVENTO_MEDICO_DESATIVADO,
    EVENTO_SENHA_REDEFINIDA_ADMIN,
    EVENTO_SENHA_TROCADA_PROPRIA,
    ORIGEM_FORMULARIO,
    ORIGEM_PAINEL_ADMIN,
    RÓTULOS_TIPOS_DE_EVENTO,
    TODOS_OS_TIPOS_DE_EVENTO,
    limpar_eventos_antigos,
    registrar_evento,
)
from src.auth import autenticar, esta_bloqueado, gerar_hash_senha, semear_usuarios_iniciais, validar_senha_forte
from src.database import (
    buscar_atestado_por_codigo,
    buscar_usuario_por_login,
    contar_oauth_access_tokens_ativos,
    criar_usuario,
    definir_status_usuario,
    init_db,
    listar_atestados_por_crm,
    listar_eventos_auditoria,
    listar_medicos,
    redefinir_senha_usuario,
    revogar_atestado,
    revogar_oauth_access_tokens,
    revogar_token_api,
    salvar_atestado,
    salvar_token_api,
)
from src.qr_generator import gerar_qr
from src.retencao import (
    aplicar_retencao_automatica,
    anonimizar_atestado_manual,
    dias_retencao_atestados_configurados,
    excluir_atestado_manual,
)
from src.urls import url_base as _url_base_compartilhada, url_qr_publica
from src.api_tokens import gerar_token, hash_token, mascarar_token

# ---------------------------------------------------------------------------
# Paleta oficial AmorSaúde
# ---------------------------------------------------------------------------
COR_PRIMARIA = "#5FC2D4"    # verde-água / teal — cor principal da marca
COR_SECUNDARIA = "#D74846"  # coral — destaques
COR_CTA = "#D53A31"         # vermelho — botões de ação
COR_TEXTO = "#525050"       # texto principal
COR_FUNDO_CLARO = "#EAF7F9"  # fundo das seções
COR_BRANCO = "#FFFFFF"
COR_BORDA = "#D7ECEF"
COR_AMBAR = "#B9770E"       # âmbar neutro — usado apenas no estado "não encontrado"
COR_AMBAR_FUNDO = "#FDF2E3"
COR_NEUTRA = "#7A7A7A"      # cinza neutro — usado apenas no estado "dados removidos" (anonimizado)
COR_NEUTRA_FUNDO = "#EFEFEF"

# ---------------------------------------------------------------------------
# Biblioteca de ícones Lucide (SVG inline — traço de linha, sem preenchimento)
# Todos os ícones compartilham viewBox="0 0 24 24", stroke-width=2.
# ---------------------------------------------------------------------------
_ICO: dict[str, str] = {
    # estados de atestado
    "check-circle":   '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "x-circle":       '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "alert-triangle": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    # navegação / seções
    "shield-check":   '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "info":           '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "key":            '<path d="M2 18v3c0 .6.4 1 1 1h4v-3h3v-3h2l1.4-1.4a6.5 6.5 0 1 0-4-4Z"/><circle cx="16.5" cy="7.5" r=".5" fill="currentColor"/>',
    "plug":           '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/>',
    "user-plus":      '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" x2="19" y1="8" y2="14"/><line x1="22" x2="16" y1="11" y2="11"/>',
    "list":           '<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/><line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/><line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>',
    "bar-chart":      '<line x1="18" x2="18" y1="20" y2="10"/><line x1="12" x2="12" y1="20" y2="4"/><line x1="6" x2="6" y1="20" y2="14"/>',
    "folder-open":    '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "file-plus":      '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M12 12v6"/>',
    # cards do dashboard
    "file-text":      '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    "calendar":       '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>',
    "sun":            '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "bed":            '<path d="M3 20v-8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v8"/><path d="M5 10V6a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4"/><path d="M3 18h18"/>',
    "users":          '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    # ações
    "printer":        '<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/><rect width="12" height="8" x="6" y="14"/>',
    "clipboard":      '<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    "download":       '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
    "qr-code":        '<rect width="5" height="5" x="3" y="3" rx="1"/><rect width="5" height="5" x="16" y="3" rx="1"/><rect width="5" height="5" x="3" y="16" rx="1"/><path d="M21 16h-3a2 2 0 0 0-2 2v3"/><path d="M21 21v.01"/><path d="M12 7v3a2 2 0 0 1-2 2H7"/><path d="M3 12h.01"/><path d="M12 3h.01"/><path d="M12 16v.01"/><path d="M16 12h1"/><path d="M21 12v.01"/><path d="M12 21v-1"/>',
    "ban":            '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    "refresh-cw":     '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "plus-circle":    '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/>',
    "lock":           '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "search":         '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "bot":            '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
    "globe":          '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
    "stethoscope":    '<path d="M4.8 2.3A.3.3 0 1 0 5 2H4a2 2 0 0 0-2 2v5a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6V4a2 2 0 0 0-2-2h-1a.2.2 0 1 0 .3.3"/><path d="M8 15v1a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6v-4"/><circle cx="20" cy="10" r="2"/>',
    # retenção/exclusão de dados (LGPD, parte 4)
    "trash-2":        '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>',
    "user-x":         '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="17" x2="22" y1="8" y2="13"/><line x1="22" x2="17" y1="8" y2="13"/>',
}


def _svg(nome: str, tamanho: int = 16, cor: str = COR_PRIMARIA, estilo_extra: str = "") -> str:
    """Retorna um ícone Lucide como SVG inline na cor e tamanho especificados."""
    paths = _ICO.get(nome, "")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{tamanho}" height="{tamanho}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{cor}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'style="display:inline-block; vertical-align:middle; flex-shrink:0; {estilo_extra}">'
        f'{paths}'
        f'</svg>'
    )


_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo-amorsaude.png"

# ---------------------------------------------------------------------------
# Configuração da página
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AmorSaúde — Atestados",
    page_icon=":material/medical_services:",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Inicialização do banco (idempotente)
# ---------------------------------------------------------------------------
init_db()
semear_usuarios_iniciais()
limpar_eventos_antigos()
aplicar_retencao_automatica()


# ---------------------------------------------------------------------------
# Identidade visual — CSS global + helpers de marca
# ---------------------------------------------------------------------------

def _injetar_estilo() -> None:
    # Carrega Nunito Sans do Google Fonts — funciona em dev e em produção
    st.markdown(
        """
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:ital,opsz,wght@0,6..12,300;0,6..12,400;0,6..12,600;0,6..12,700;0,6..12,800;0,6..12,900;1,6..12,400&display=swap" rel="stylesheet">
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <style>
        /* ─────────────────────────────────────────────
           TIPOGRAFIA — Nunito Sans, hierarquia em 4 níveis
           ───────────────────────────────────────────── */
        html, body, [class*="css"], .stApp,
        button, input, textarea, select {{
            font-family: 'Nunito Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
        }}
        /* Nível 1 — título de página / status principal */
        h1 {{
            font-size: 1.75rem !important; font-weight: 800 !important;
            line-height: 1.2 !important; color: {COR_TEXTO} !important;
            letter-spacing: -0.01em !important;
        }}
        /* Nível 2 — cabeçalho de seção */
        h2 {{
            font-size: 1.375rem !important; font-weight: 700 !important;
            line-height: 1.25 !important; color: {COR_TEXTO} !important;
        }}
        /* Nível 3 — sub-seção / card title */
        h3 {{
            font-size: 1.0625rem !important; font-weight: 700 !important;
            line-height: 1.35 !important; color: {COR_TEXTO} !important;
            letter-spacing: 0.005em !important;
        }}
        p, li {{
            font-size: 0.9375rem !important; line-height: 1.65 !important;
            color: {COR_TEXTO};
        }}
        /* Labels de campos — menores, peso médio, cor suave */
        [data-testid="stTextInput"] label,
        [data-testid="stNumberInput"] label,
        [data-testid="stDateInput"] label,
        [data-testid="stTextArea"] label,
        [data-testid="stRadio"] > label,
        [data-testid="stRadio"] > div > label {{
            font-size: 0.8125rem !important;
            font-weight: 600 !important;
            letter-spacing: 0.02em !important;
            color: {COR_TEXTO} !important;
            opacity: 0.7 !important;
        }}

        /* ─────────────────────────────────────────────
           FUNDO E CORES
           ───────────────────────────────────────────── */
        .stApp {{
            background-color: {COR_FUNDO_CLARO} !important;
        }}
        [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{
            background-color: {COR_FUNDO_CLARO} !important;
        }}
        [data-testid="stHeader"] {{
            background-color: transparent !important;
        }}
        html, body, [class*="css"] {{
            color: {COR_TEXTO};
        }}

        /* ─────────────────────────────────────────────
           FORMULÁRIOS
           ───────────────────────────────────────────── */
        [data-testid="stForm"] {{
            background-color: {COR_BRANCO} !important;
            border-radius: 12px !important;
            padding: 1.5rem !important;
            border: 1px solid {COR_BORDA} !important;
        }}

        /* Campos — fundo branco, borda suave, foco verde-água */
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input,
        [data-testid="stTextArea"] textarea,
        [data-baseweb="input"],
        [data-baseweb="select"] > div {{
            background-color: {COR_BRANCO} !important;
            color: {COR_TEXTO} !important;
            border: 1.5px solid {COR_BORDA} !important;
            border-radius: 8px !important;
            font-family: 'Nunito Sans', sans-serif !important;
            font-size: 0.9375rem !important;
        }}
        [data-testid="stTextInput"] input:focus,
        [data-testid="stNumberInput"] input:focus,
        [data-testid="stTextArea"] textarea:focus {{
            border-color: {COR_PRIMARIA} !important;
            box-shadow: 0 0 0 3px rgba(95,194,212,0.18) !important;
            outline: none !important;
        }}
        [data-testid="stTextInput"], [data-testid="stNumberInput"],
        [data-testid="stDateInput"], [data-testid="stTextArea"] {{
            background-color: transparent !important;
        }}

        /* ─────────────────────────────────────────────
           EXPANDER
           ───────────────────────────────────────────── */
        [data-testid="stExpander"] {{
            background-color: {COR_BRANCO} !important;
            border: 1px solid {COR_BORDA} !important;
            border-radius: 10px !important;
        }}
        [data-testid="stExpander"] summary {{
            color: {COR_PRIMARIA} !important;
            font-weight: 600 !important;
            font-size: 0.9375rem !important;
        }}

        /* ─────────────────────────────────────────────
           MICROINTERAÇÕES — transições globais
           ───────────────────────────────────────────── */
        button, input, textarea, select,
        [data-testid="stVerticalBlockBorderWrapper"],
        [data-testid="stExpander"] {{
            transition: all 160ms cubic-bezier(0.25, 0.46, 0.45, 0.94) !important;
        }}
        /* Inputs: só border-color + shadow para não interferir no layout */
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input,
        [data-testid="stTextArea"] textarea {{
            transition: border-color 160ms ease, box-shadow 160ms ease !important;
        }}

        /* ─────────────────────────────────────────────
           CARDS — hover com "lift" suave
           ───────────────────────────────────────────── */
        [data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 12px !important;
            box-shadow: 0 1px 4px rgba(95,194,212,0.08), 0 2px 12px rgba(0,0,0,0.04) !important;
            background-color: {COR_BRANCO} !important;
            border-color: {COR_BORDA} !important;
            will-change: transform, box-shadow;
        }}
        [data-testid="stVerticalBlockBorderWrapper"]:hover {{
            box-shadow: 0 4px 16px rgba(95,194,212,0.16), 0 8px 24px rgba(0,0,0,0.07) !important;
            transform: translateY(-2px) !important;
            border-color: rgba(95,194,212,0.4) !important;
        }}

        /* ─────────────────────────────────────────────
           EXPANDER — hover suave no summary
           ───────────────────────────────────────────── */
        [data-testid="stExpander"] summary:hover {{
            opacity: 0.75 !important;
        }}

        /* ─────────────────────────────────────────────
           BOTÕES — peso tipográfico + microinterações
           ───────────────────────────────────────────── */
        button[kind="primary"] {{
            background-color: {COR_CTA} !important;
            border-color: {COR_CTA} !important;
            color: {COR_BRANCO} !important;
            font-family: 'Nunito Sans', sans-serif !important;
            font-weight: 700 !important;
            font-size: 0.9375rem !important;
            border-radius: 8px !important;
            letter-spacing: 0.01em !important;
            will-change: transform, box-shadow;
        }}
        button[kind="primary"]:hover {{
            background-color: #b8241c !important;
            border-color: #b8241c !important;
            box-shadow: 0 4px 12px rgba(213,58,49,0.30) !important;
            transform: translateY(-1px) !important;
        }}
        button[kind="primary"]:active {{
            transform: translateY(0px) !important;
            box-shadow: 0 1px 4px rgba(213,58,49,0.20) !important;
        }}
        button[kind="primary"]:focus-visible {{
            outline: 2px solid {COR_CTA} !important;
            outline-offset: 2px !important;
            box-shadow: 0 0 0 4px rgba(213,58,49,0.20) !important;
        }}
        button[kind="secondary"] {{
            background-color: {COR_BRANCO} !important;
            border: 1.5px solid {COR_PRIMARIA} !important;
            color: {COR_PRIMARIA} !important;
            font-family: 'Nunito Sans', sans-serif !important;
            font-weight: 600 !important;
            font-size: 0.9375rem !important;
            border-radius: 8px !important;
            will-change: transform, box-shadow;
        }}
        button[kind="secondary"]:hover {{
            background-color: {COR_FUNDO_CLARO} !important;
            border-color: #3fa8bc !important;
            color: #3a96a8 !important;
            box-shadow: 0 2px 8px rgba(95,194,212,0.18) !important;
            transform: translateY(-1px) !important;
        }}
        button[kind="secondary"]:active {{
            transform: translateY(0px) !important;
            box-shadow: none !important;
        }}
        button[kind="secondary"]:focus-visible {{
            outline: 2px solid {COR_PRIMARIA} !important;
            outline-offset: 2px !important;
            box-shadow: 0 0 0 4px rgba(95,194,212,0.22) !important;
        }}

        /* Botões HTML customizados (imprimir, copiar link) */
        button.amorsaude-btn-outline {{
            transition: background-color 160ms ease, box-shadow 160ms ease,
                        transform 160ms ease, border-color 160ms ease !important;
            will-change: transform, box-shadow;
        }}
        button.amorsaude-btn-outline:hover {{
            background-color: {COR_FUNDO_CLARO} !important;
            box-shadow: 0 2px 8px rgba(95,194,212,0.18) !important;
            transform: translateY(-1px) !important;
        }}
        button.amorsaude-btn-outline:active {{
            transform: translateY(0px) !important;
            box-shadow: none !important;
        }}
        button.amorsaude-btn-outline:focus-visible {{
            outline: 2px solid {COR_PRIMARIA} !important;
            outline-offset: 2px !important;
        }}

        /* ─────────────────────────────────────────────
           MÉTRICAS / CAPTIONS
           ───────────────────────────────────────────── */
        [data-testid="stMetricValue"] {{
            color: {COR_PRIMARIA} !important;
            font-weight: 800 !important;
            font-size: 1.75rem !important;
        }}
        [data-testid="stMetricLabel"] {{
            color: {COR_TEXTO} !important;
            font-size: 0.8125rem !important;
        }}
        [data-testid="stCaption"], .stCaption p {{
            font-size: 0.8125rem !important;
            color: {COR_TEXTO} !important;
            opacity: 0.68 !important;
        }}
        /* Código inline */
        code {{
            font-size: 0.875rem !important;
            background-color: {COR_FUNDO_CLARO} !important;
            border: 1px solid {COR_BORDA} !important;
            border-radius: 4px !important;
            padding: 0.1em 0.35em !important;
            color: {COR_PRIMARIA} !important;
        }}

        /* ─────────────────────────────────────────────
           DIVISORES — ritmo de 8pt (2rem = 32px de gap)
           ───────────────────────────────────────────── */
        hr {{
            border-color: {COR_BORDA} !important;
            margin: 2rem 0 !important;
        }}

        /* ─────────────────────────────────────────────
           BASE — box-sizing + overflow global
           ───────────────────────────────────────────── */
        *, *::before, *::after {{
            box-sizing: border-box !important;
        }}
        html, body {{
            overflow-x: hidden !important;
            max-width: 100vw !important;
        }}
        .stApp, [data-testid="stAppViewBlockContainer"] {{
            overflow-x: hidden !important;
        }}

        /* ─────────────────────────────────────────────
           MOBILE — responsividade (≤ 640px)
           ───────────────────────────────────────────── */
        @media (max-width: 640px) {{
            /* Padding lateral compacto */
            [data-testid="stAppViewBlockContainer"] {{
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
            }}

            /* Tipografia levemente reduzida no mobile */
            h1 {{ font-size: 1.25rem !important; }}
            h2 {{ font-size: 1.0625rem !important; }}
            p, li {{ font-size: 0.875rem !important; }}

            /* Cabeçalho: padding compacto */
            .amorsaude-cabecalho {{
                padding: 0.75rem 1rem !important;
                margin-bottom: 1.25rem !important;
                border-radius: 10px !important;
            }}
            .amorsaude-logo-wrap {{
                padding: 0.25rem 0.5rem !important;
            }}

            /* Selo de verificação: mais compacto */
            .amorsaude-selo {{
                padding: 1.25rem 0.5rem 0.75rem !important;
            }}
            .amorsaude-selo-circulo {{
                width: 64px !important;
                height: 64px !important;
            }}

            /* Formulários: padding menor */
            [data-testid="stForm"] {{
                padding: 1rem 0.875rem !important;
            }}

            /* 5 colunas → 2 por linha (cards de resumo do dashboard) */
            [data-testid="stHorizontalBlock"]:has(
                > [data-testid="column"]:nth-child(5)
            ) {{
                flex-wrap: wrap !important;
                gap: 0.5rem !important;
            }}
            [data-testid="stHorizontalBlock"]:has(
                > [data-testid="column"]:nth-child(5)
            ) > [data-testid="column"] {{
                flex: 1 1 calc(50% - 0.5rem) !important;
                min-width: calc(50% - 0.5rem) !important;
                max-width: calc(50% - 0.25rem) !important;
            }}

            /* 4 colunas → 2 por linha (metadados do atestado) */
            [data-testid="stHorizontalBlock"]:has(
                > [data-testid="column"]:nth-child(4)
            ):not(:has(> [data-testid="column"]:nth-child(5))) {{
                flex-wrap: wrap !important;
                gap: 0.25rem !important;
            }}
            [data-testid="stHorizontalBlock"]:has(
                > [data-testid="column"]:nth-child(4)
            ):not(:has(> [data-testid="column"]:nth-child(5)))
            > [data-testid="column"] {{
                flex: 1 1 calc(50% - 0.25rem) !important;
                min-width: calc(50% - 0.25rem) !important;
            }}

            /* Alvos de toque mínimo 44px */
            button[kind="primary"],
            button[kind="secondary"] {{
                min-height: 44px !important;
                padding-top: 0.625rem !important;
                padding-bottom: 0.625rem !important;
            }}
            button.amorsaude-btn-outline {{
                min-height: 44px !important;
                padding-top: 0.625rem !important;
                padding-bottom: 0.625rem !important;
            }}

            /* Inputs confortáveis para toque */
            [data-testid="stTextInput"] input,
            [data-testid="stNumberInput"] input,
            [data-testid="stDateInput"] input {{
                min-height: 44px !important;
                font-size: 1rem !important;
            }}
        }}

        /* ─────────────────────────────────────────────
           IMPRESSÃO
           ───────────────────────────────────────────── */
        @media print {{
            [data-testid="stToolbar"], [data-testid="stStatusWidget"],
            [data-testid="stDecoration"], header[data-testid="stHeader"],
            .amorsaude-nao-imprimir {{
                display: none !important;
            }}
            .stApp {{
                background-color: {COR_BRANCO} !important;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data
def _logo_base64() -> str | None:
    """Lê a logo e retorna em base64 para embutir no HTML. None se não existir."""
    if _LOGO_PATH.exists():
        return base64.b64encode(_LOGO_PATH.read_bytes()).decode()
    return None


def _logo_html(altura_px: int = 48, cor_fallback: str = COR_PRIMARIA) -> str:
    """Tag <img> com a logo, ou texto 'AmorSaúde' estilizado se o arquivo não existir."""
    b64 = _logo_base64()
    if b64:
        return (
            f'<img src="data:image/png;base64,{b64}" '
            f'style="height:{altura_px}px; width:auto; max-width:none; display:block;" '
            f'alt="AmorSaúde" />'
        )
    return (
        f'<span style="font-size:{altura_px * 0.55}px; font-weight:800; '
        f'color:{cor_fallback}; font-family:\'Nunito Sans\',sans-serif;">AmorSaúde</span>'
    )


def _barra_cabecalho(conteudo_direita: str = "") -> None:
    """
    Barra de cabeçalho com fundo verde-água + logo à esquerda, usada no dashboard e na verificação.

    Construída como uma única linha (sem quebras/indentação entre as tags).
    Quando `conteudo_direita` vem vazio (tela de verificação), uma versão
    indentada e multi-linha faz o Markdown do Streamlit interpretar a linha
    em branco + `</div>` indentado como um bloco de código, exibindo o texto
    "</div>" na tela. Uma única linha elimina essa ambiguidade.
    """
    # A logo tem a palavra "amor" na mesma cor verde-água da marca — sobre o
    # fundo teal do cabeçalho ela ficaria "invisível" (mesma cor do fundo).
    # Por isso a logo fica sobre uma placa branca, como no cartão de login.
    html_str = (
        f'<div class="amorsaude-cabecalho" style="background-color:{COR_PRIMARIA}; padding:1rem 1.5rem; '
        f'border-radius:14px; display:flex; align-items:center; '
        f'justify-content:space-between; margin-bottom:2rem; gap:1rem; '
        f'box-shadow:0 2px 12px rgba(0,0,0,0.10);">'
        f'<div class="amorsaude-logo-wrap" style="background-color:{COR_BRANCO}; border-radius:8px; '
        f'padding:0.375rem 0.75rem; display:flex; align-items:center; '
        f'min-width:0; flex-shrink:0;">'
        f'{_logo_html(38, cor_fallback=COR_PRIMARIA)}'
        f'</div>'
        f'<div style="color:{COR_BRANCO}; text-align:right; font-family:\'Nunito Sans\',sans-serif;">{conteudo_direita}</div>'
        f'</div>'
    )
    st.markdown(html_str, unsafe_allow_html=True)


def _caixa_mensagem(texto: str, cor_fundo: str, cor_texto: str = COR_BRANCO, icone: str = "") -> None:
    """Caixa de mensagem customizada (usada para o estado de atestado inválido em coral)."""
    icone_html = f'<span style="margin-right:0.5rem; vertical-align:middle; flex-shrink:0;">{icone}</span>' if icone else ""
    st.markdown(
        f"""
        <div style="background-color:{cor_fundo}; color:{cor_texto}; padding:1rem 1.5rem;
                    border-radius:10px; font-weight:700; font-size:0.9375rem;
                    font-family:'Nunito Sans',sans-serif; margin:0.5rem 0;
                    display:flex; align-items:center; gap:0.75rem;">
            {icone_html}{texto}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _selo_status(icone_svg: str, titulo: str, cor: str, cor_fundo: str, subtitulo: str = "") -> None:
    """Selo grande e inequívoco de status, no padrão de validadores oficiais (gov.br/ITI, Atesta CFM).

    `subtitulo` é sempre escapado aqui — hardening defensivo, mesmo que os
    chamadores atuais já escapem valores dinâmicos (ex.: revogado_em) antes
    de passá-los, para evitar regressões se um novo call site esquecer disso.
    """
    subtitulo_html = (
        f'<p style="color:{COR_TEXTO}; font-size:0.9375rem; max-width:32rem; '
        f'margin:0.75rem auto 0 auto; line-height:1.6; opacity:0.85; '
        f'font-family:\'Nunito Sans\',sans-serif;">{html.escape(subtitulo)}</p>'
        if subtitulo
        else ""
    )
    st.markdown(
        f"""
        <div class="amorsaude-selo" style="text-align:center; padding:2rem 1rem 1rem 1rem; font-family:'Nunito Sans',sans-serif;">
            <div class="amorsaude-selo-circulo" style="width:80px; height:80px; border-radius:50%; background-color:{cor_fundo};
                        display:flex; align-items:center; justify-content:center; margin:0 auto 1.25rem auto;
                        box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                {icone_svg}
            </div>
            <h1 style="color:{cor}; margin:0; font-size:1.625rem; font-weight:800;
                       letter-spacing:-0.01em; font-family:'Nunito Sans',sans-serif;">{titulo}</h1>
            {subtitulo_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _frase_confianca() -> None:
    ico_shield = _svg("shield-check", 13, COR_PRIMARIA, "flex-shrink:0")
    ico_lock   = _svg("lock",         12, COR_PRIMARIA, "flex-shrink:0")
    ico_eye    = _svg("eye-off",      12, COR_PRIMARIA, "flex-shrink:0")

    def _badge(icone: str, texto: str) -> str:
        return (
            f'<span style="display:inline-flex; align-items:center; gap:0.3rem; '
            f'background:{COR_FUNDO_CLARO}; border:1px solid {COR_BORDA}; '
            f'border-radius:20px; padding:0.25rem 0.625rem; white-space:nowrap;">'
            f'{icone}<span>{texto}</span></span>'
        )

    st.markdown(
        f"""
        <div style="display:flex; flex-wrap:wrap; justify-content:center; gap:0.5rem;
                    margin:0.5rem 0 1.5rem 0; font-family:'Nunito Sans',sans-serif;
                    font-size:0.75rem; font-weight:600; color:{COR_PRIMARIA};
                    letter-spacing:0.01em;">
            {_badge(ico_shield, "Registrado na AmorSaúde")}
            {_badge(ico_lock,   "Conexão segura")}
            {_badge(ico_eye,    "Consulta não registrada")}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _bloco_metadados_verificacao(codigo: str, rotulo_codigo: str = "Código de autenticação") -> None:
    """Bloco discreto de metadados da consulta, no padrão de recibo de verificação oficial."""
    agora = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")
    st.markdown(
        f"""
        <div style="background-color:{COR_FUNDO_CLARO}; border:1px solid {COR_BORDA};
                    border-radius:8px; padding:1rem 1.25rem; margin-top:1.5rem;
                    font-size:0.8125rem; font-family:'Nunito Sans',sans-serif; color:{COR_TEXTO};">
            <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:0.5rem 1.5rem;">
                <span><strong style="font-weight:700;">Verificado em:</strong>&nbsp;{agora}</span>
                <span style="word-break:break-all;"><strong style="font-weight:700;">{rotulo_codigo}:</strong>&nbsp;<code style="font-size:0.75rem; background:{COR_BRANCO}; padding:0.1em 0.3em; border-radius:4px; border:1px solid {COR_BORDA};">{html.escape(codigo)}</code></span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _campo_dado(rotulo: str, valor: str) -> None:
    """Par rótulo/valor sem truncar texto longo (ao contrário de st.metric)."""
    st.markdown(
        f"""
        <div style="margin-bottom:1.25rem; font-family:'Nunito Sans',sans-serif;">
            <div style="color:{COR_TEXTO}; opacity:0.6; font-size:0.75rem; font-weight:600;
                        letter-spacing:0.04em; text-transform:uppercase; margin-bottom:0.25rem;">{rotulo}</div>
            <div style="color:{COR_TEXTO}; font-size:1.1875rem; font-weight:700; line-height:1.3;
                        word-break:break-word;">{html.escape(str(valor))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _campo_cid_protegido() -> None:
    """Campo especial para o CID — exibe ícone de cadeado em vez do valor real."""
    icone = _svg("lock", 14, COR_TEXTO, "opacity:0.45; margin-right:0.375rem; flex-shrink:0")
    st.markdown(
        f"""
        <div style="margin-bottom:1.25rem; font-family:'Nunito Sans',sans-serif;">
            <div style="color:{COR_TEXTO}; opacity:0.6; font-size:0.75rem; font-weight:600;
                        letter-spacing:0.04em; text-transform:uppercase; margin-bottom:0.25rem;">Diagnóstico (CID)</div>
            <div style="color:{COR_TEXTO}; font-size:0.9375rem; font-weight:600;
                        display:flex; align-items:center; opacity:0.65;">
                {icone}<span>Protegido por sigilo médico</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _bloco_como_funciona() -> None:
    ico_info    = _svg("info",     14, COR_PRIMARIA, "flex-shrink:0")
    ico_shield  = _svg("shield",   13, COR_PRIMARIA, "flex-shrink:0; opacity:0.7")
    ico_eye_off = _svg("eye-off",  13, COR_PRIMARIA, "flex-shrink:0; opacity:0.7")
    ico_zap     = _svg("zap",      13, COR_PRIMARIA, "flex-shrink:0; opacity:0.7")

    def _item(icone: str, texto: str) -> str:
        return (
            f'<div style="display:flex; align-items:flex-start; gap:0.5rem; '
            f'margin-top:0.5rem; font-size:0.8125rem; line-height:1.5; opacity:0.82;">'
            f'{icone}<span>{texto}</span></div>'
        )

    st.markdown(
        f"""
        <div style="background-color:{COR_BRANCO}; border:1px solid {COR_BORDA}; border-radius:10px;
                    padding:1rem 1.25rem; margin-top:1.5rem; color:{COR_TEXTO};
                    font-family:'Nunito Sans',sans-serif;">
            <div style="display:flex; align-items:center; gap:0.5rem; font-weight:700;
                        font-size:0.875rem; color:{COR_PRIMARIA}; margin-bottom:0.25rem;">
                {ico_info} Como funciona esta verificação
            </div>
            {_item(ico_zap,     "Verificação em tempo real diretamente na base de dados AmorSaúde — não é um PDF e não pode ser falsificado.")}
            {_item(ico_shield,  "O resultado exibido é o mesmo a cada leitura do QR Code ou do link, garantindo autenticidade integral.")}
            {_item(ico_eye_off, "Nenhum dado de quem realiza esta consulta é coletado, registrado ou armazenado.")}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _botao_imprimir() -> None:
    """Botão que abre a caixa de impressão do navegador para gerar um comprovante limpo."""
    svg_printer = _svg("printer", 15, COR_PRIMARIA, "margin-right:0.4rem; vertical-align:middle")
    html_conteudo = f"""
    <button id="btn-imprimir-comprovante" class="amorsaude-btn-outline"
            style="background-color:{COR_BRANCO}; color:{COR_PRIMARIA}; border:1.5px solid {COR_PRIMARIA};
                   border-radius:8px; padding:0.5rem 1rem; cursor:pointer; font-size:0.875rem;
                   font-weight:700; width:100%; font-family:'Nunito Sans',sans-serif;
                   letter-spacing:0.01em;
                   display:flex; align-items:center; justify-content:center; gap:0.375rem;">
        {svg_printer} Imprimir comprovante
    </button>
    <script>
        document.getElementById("btn-imprimir-comprovante").addEventListener("click", function() {{
            window.parent.print();
        }});
    </script>
    """
    components.html(html_conteudo, height=48)


def _botao_copiar_link(url: str, chave: str) -> None:
    """
    Botão que copia a URL de verificação para a área de transferência via JS.

    A URL nunca é interpolada dentro do bloco <script> — fica apenas num
    atributo HTML (data-url) devidamente escapado com html.escape(), lido em
    runtime via getAttribute. Isso evita que caracteres especiais (aspas,
    `</script>`, tags) quebrem o HTML ou permitam injeção de script.
    """
    # `chave` (código do atestado) nunca é usado cru em HTML/JS: um id determinístico
    # e seguro (hash hexadecimal) é derivado dele. A URL nunca entra no <script> —
    # fica apenas num atributo HTML escapado (data-url) e é lida em runtime via
    # getAttribute, eliminando qualquer risco de payload quebrar a tag <script>.
    id_seguro = "btn-copiar-" + hashlib.sha256(chave.encode()).hexdigest()[:16]
    url_escapada = html.escape(url, quote=True)

    svg_clipboard = _svg("clipboard", 14, COR_PRIMARIA)
    # O svg é armazenado como atributo HTML (html.escape escapa < > " para entidades).
    # getAttribute() devolve o valor decodificado; innerHTML re-interpreta as tags — funciona.
    svg_default_escaped = html.escape(svg_clipboard + "&nbsp;Copiar link", quote=True)

    html_conteudo = f"""
    <button id="{id_seguro}" class="amorsaude-btn-outline" data-url="{url_escapada}" data-default="{svg_default_escaped}"
            style="background-color:{COR_BRANCO}; color:{COR_PRIMARIA};
                   border:1.5px solid {COR_PRIMARIA}; border-radius:8px;
                   padding:0.5rem 0.75rem; cursor:pointer; font-size:0.8125rem;
                   font-weight:700; width:100%; font-family:'Nunito Sans',sans-serif;
                   letter-spacing:0.01em;
                   display:flex; align-items:center; justify-content:center; gap:0.375rem;">
        {svg_clipboard}&nbsp;Copiar link
    </button>
    <script>
        (function() {{
            var btn = document.getElementById("{id_seguro}");
            btn.addEventListener("click", function() {{
                var url = btn.getAttribute("data-url");
                navigator.clipboard.writeText(url);
                btn.innerHTML = "&#10003;&nbsp;Copiado!";
                setTimeout(function() {{
                    btn.innerHTML = btn.getAttribute("data-default");
                }}, 1500);
            }});
        }})();
    </script>
    """
    components.html(html_conteudo, height=42)


def _secao_token_api(usuario_alvo: dict, quem_gerencia: str) -> None:
    """
    Bloco de gestão do token de API de um médico — usado tanto no painel do
    administrador (gerenciando o token de outro médico) quanto no dashboard
    do próprio médico (gerenciando o seu). `quem_gerencia` é só um prefixo
    para as chaves dos widgets, para não colidir quando a mesma função é
    chamada várias vezes na mesma tela (ex.: um card por médico no admin).
    """
    chave_gerar = f"gerar_token_{quem_gerencia}_{usuario_alvo['id']}"
    chave_confirmar_revogar = f"confirmar_revogar_token_{quem_gerencia}_{usuario_alvo['id']}"
    chave_token_novo = f"token_novo_{quem_gerencia}_{usuario_alvo['id']}"

    tem_token = bool(usuario_alvo.get("api_token_hash"))
    token_recem_gerado = st.session_state.get(chave_token_novo)

    with st.expander(f"Token de API — {usuario_alvo['nome']}", expanded=bool(token_recem_gerado)):
        st.markdown(
            "O token de API identifica este médico perante o endpoint de registro "
            "automático de atestados (ver seção **API / Integrações**). Trate-o como "
            "uma senha: qualquer chamada feita com ele é registrada em nome deste médico."
        )

        if token_recem_gerado:
            st.warning(
                "Copie o token agora — por segurança, ele não será exibido novamente. "
                "Ao gerar um novo token, este deixa de funcionar.",
            )
            st.code(token_recem_gerado, language=None)
            if st.button("Já copiei, ocultar", key=f"ocultar_{chave_token_novo}", type="secondary"):
                st.session_state.pop(chave_token_novo, None)
                st.rerun()
        elif tem_token:
            st.markdown(
                f"**Status:** ativo · terminando em `{html.escape(usuario_alvo.get('api_token_ultimos4') or '')}` · "
                f"gerado em {html.escape(str(usuario_alvo.get('api_token_criado_em') or ''))}"
            )
        else:
            st.caption("Nenhum token de API gerado ainda para este médico.")

        if not token_recem_gerado:
            col_gerar, col_revogar = st.columns(2)
            with col_gerar:
                rotulo = "Gerar novo token" if tem_token else "Gerar token de API"
                if st.button(rotulo, key=chave_gerar, use_container_width=True, type="primary"):
                    novo_token = gerar_token()
                    salvar_token_api(usuario_alvo["id"], hash_token(novo_token), novo_token[-4:])
                    st.session_state[chave_token_novo] = novo_token
                    st.rerun()
            with col_revogar:
                if tem_token:
                    if st.button("Revogar token", key=f"revogar_btn_{chave_confirmar_revogar}", use_container_width=True, type="secondary"):
                        st.session_state[chave_confirmar_revogar] = True
                        st.rerun()

        if st.session_state.get(chave_confirmar_revogar):
            st.warning("Tem certeza que deseja revogar este token? Chamadas de API feitas com ele passam a ser recusadas imediatamente.")
            col_sim, col_nao = st.columns(2)
            with col_sim:
                if st.button("Sim, revogar", key=f"sim_{chave_confirmar_revogar}", use_container_width=True, type="primary"):
                    revogar_token_api(usuario_alvo["id"])
                    st.session_state.pop(chave_confirmar_revogar, None)
                    st.success("Token revogado.")
                    st.rerun()
            with col_nao:
                if st.button("Cancelar", key=f"nao_{chave_confirmar_revogar}", use_container_width=True, type="secondary"):
                    st.session_state.pop(chave_confirmar_revogar, None)
                    st.rerun()


def _secao_api_integracoes() -> None:
    """Explicação em português simples de como usar a API de registro programático."""
    with st.expander("API / Integrações"):
        endereco_registro = f"{_url_base()}atestados"
        endereco_qr = f"{_url_base()}atestados/{{codigo}}/qrcode.png"
        st.markdown(
            f"""
Além do formulário acima, é possível registrar atestados **automaticamente**, de um
sistema externo (por exemplo, uma automação que preenche uma "ficha padrão" e
gera um documento no Canva). Isso é feito chamando um endereço da API com o
**token de API do médico** (gere um na seção "Token de API" acima).

**1. Endereço para registrar um atestado**

```
POST {endereco_registro}
Authorization: Bearer SEU_TOKEN_AQUI
Content-Type: application/json
```

**2. Dados a enviar** (formato JSON):
- `nome_paciente` — nome completo do paciente
- `cid` — código CID
- `data_emissao` — data no formato `AAAA-MM-DD`
- período de afastamento: **ou** `dias_afastamento` (número de dias) **ou** `data_inicio` + `data_fim` (formato `AAAA-MM-DD`)

Exemplo de corpo da requisição:
```json
{{
  "nome_paciente": "João da Silva",
  "cid": "J18.9",
  "data_emissao": "2026-07-09",
  "dias_afastamento": 3
}}
```

**3. O que a API devolve**, se o token for válido: o código único do atestado,
o link público de verificação e o link público da imagem do QR Code (pronto
para ser baixado por outro sistema, como o Canva):
```json
{{
  "codigo": "abc123...",
  "url_verificacao": "https://.../?codigo=abc123...",
  "qr_code_url": "{endereco_qr}"
}}
```

O atestado criado dessa forma é idêntico a um emitido pelo formulário: aparece
no dashboard do médico dono do token e pode ser revogado normalmente.

**4. Se o token for inválido, ausente ou pertencer a um médico desativado**, a
API recusa o registro (nada é salvo) e devolve um erro claro.

**Importante:** o token é uma credencial sensível — não compartilhe, não cole em
lugares públicos, e gere um novo (o que invalida o anterior) se desconfiar que
vazou.

---

### Conectar diretamente na Claude (conversa faz o registro por você)

Além da API acima (para sistemas/automações), também é possível deixar a
própria Claude registrar atestados durante uma conversa, sem nenhum código —
usando um **conector MCP**. Depois de conectado, basta pedir à Claude algo como
*"registre um atestado para [paciente], CID [código], hoje, 3 dias de
afastamento"* e ela chama a ferramenta e devolve o código, o link de
verificação e o QR Code.

**Endereço do conector** (o mesmo endereço serve para todos os médicos —
não leva token nenhum, o login é feito depois, na própria Claude):

```
{endereco_registro.rsplit("/atestados", 1)[0]}/mcp
```

**Passo a passo para conectar na Claude:**
1. Na Claude, abra **Configurações → Conectores** (em claude.ai) e escolha
   **"Adicionar conector personalizado"** (Custom Connector).
2. Cole o endereço acima no campo de URL (sem token, sem parâmetros).
3. Confirme a adição e clique em **"Conectar"** — a Claude vai abrir uma
   tela de login própria deste app. Entre com o seu **usuário e senha do
   AmorSaúde** (a mesma conta de médico usada aqui no Portal) e autorize o
   acesso.
4. Depois de autorizar, a Claude já lista automaticamente a ferramenta
   "registrar_atestado" disponível nesse conector.
5. Numa conversa, ative o conector e peça à Claude para registrar o atestado
   com os dados do paciente — ela chama a ferramenta e mostra o resultado.

Um atestado criado pela Claude por esse caminho é idêntico a um emitido pelo
formulário ou pela API: aparece no seu dashboard e pode ser revogado
normalmente.

**Se você conectou este conector antes desta atualização:** o endereço antigo
(que tinha um token colado na própria URL) deixou de funcionar — a Claude
conectava, mas nunca conseguia listar a ferramenta de registro, porque aquele
formato não é reconhecido pelo mecanismo de autenticação da Claude. **Remova
o conector antigo em Configurações → Conectores e adicione-o de novo com o
endereço acima**, sem token na URL; o login passa a ser feito na tela que a
própria Claude abre.

**Revogar o acesso do conector Claude:** como a autenticação agora usa login
(e não mais o token de API), revogar o "Token de API" acima **não** afeta
o conector MCP. Para desconectar todos os acessos já concedidos à Claude (por
exemplo, se você suspeita que alguém mais teve acesso à sua conta), use o
botão abaixo.

---

### Publicar o app (obrigatório para o conector MCP ficar sempre disponível)

Enquanto o app estiver rodando apenas no **endereço de desenvolvimento**
(o que aparece durante a edição no Replit), ele fica no ar apenas enquanto o
workspace está aberto — é por isso que a Claude pode mostrar **"Não foi
possível conectar ao servidor"** ao tentar usar o conector depois de um
tempo. Para o conector (e a página pública de verificação) ficarem
disponíveis o tempo todo, é preciso **publicar** o app.

**Passo a passo para publicar:**
1. No topo do workspace do Replit, clique em **"Publish"** (ou no menu de
   três pontos, se não aparecer direto).
2. Escolha o tipo de implantação — este app já está configurado para rodar
   como um serviço sempre ativo ("Reserved VM"), que é o que ele precisa,
   porque mantém os dados salvos localmente e não pode rodar em múltiplas
   cópias ao mesmo tempo.
3. Confirme e aguarde a publicação terminar. Você vai receber um endereço
   fixo, algo como `https://SEU-APP.replit.app` (ou um domínio próprio, se
   você configurou um).
4. **Depois de publicado, o endereço do conector MCP passa a ser:**
   ```
   https://SEU-APP.replit.app/mcp
   ```
   (troque pelo endereço real que o Replit gerar para o seu app — toda vez
   que alguém acessa o app, ele monta os links automaticamente com base no
   endereço usado na hora, então não é preciso configurar nada manualmente
   depois de publicar.)
5. **Se você já tinha conectado o conector usando o endereço de
   desenvolvimento (o que aparece durante a edição, terminado em
   `.replit.dev`), remova esse conector em Configurações → Conectores da
   Claude e adicione-o de novo com o endereço publicado (`.replit.app`)
   acima.** Um conector apontando para o endereço de desenvolvimento sempre
   vai falhar assim que o workspace de edição não estiver aberto — mesmo que
   antes tenha funcionado.
            """
        )
        st.divider()
        usuario_atual = st.session_state.get("usuario")
        if usuario_atual:
            qtd_ativos = contar_oauth_access_tokens_ativos(usuario_atual["id"])
            if qtd_ativos:
                st.caption(f"Conector Claude: {qtd_ativos} acesso(s) autorizado(s) e ainda válido(s).")
                if st.button("Revogar acesso do conector Claude", key="revogar_oauth_mcp"):
                    revogar_oauth_access_tokens(usuario_atual["id"])
                    st.success("Acesso revogado. Para usar a Claude novamente, será preciso autorizar o conector outra vez.")
                    st.rerun()
            else:
                st.caption("Conector Claude: nenhum acesso autorizado no momento.")


_injetar_estilo()

# ---------------------------------------------------------------------------
# Helpers de negócio (inalterados)
# ---------------------------------------------------------------------------

def _url_base() -> str:
    """Monta a URL base do app (implementação compartilhada com a API em src/urls.py)."""
    return _url_base_compartilhada()


def _formatar_periodo(row: dict) -> str:
    if row.get("data_inicio") and row.get("data_fim"):
        return f"{row['data_inicio']} a {row['data_fim']}"
    if row.get("dias_afastamento"):
        return f"{row['dias_afastamento']} dia(s)"
    return "—"


def _gerar_csv(atestados: list[dict]) -> bytes:
    """Gera CSV (apenas apresentação/exportação — não altera a fonte de dados)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Paciente", "CID", "Data de Emissão", "Início", "Fim", "Dias de Afastamento", "Código", "Status"]
    )
    for a in atestados:
        writer.writerow(
            [
                a["nome_paciente"],
                a["cid"],
                a["data_emissao"],
                a.get("data_inicio") or "",
                a.get("data_fim") or "",
                a.get("dias_afastamento") or "",
                a["codigo"],
                "Ativo",
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# TELA 1 — Verificação pública (?codigo=XXX)
# ---------------------------------------------------------------------------

def tela_verificacao(codigo: str) -> None:
    _barra_cabecalho()

    with st.spinner("Consultando banco de dados…"):
        atestado = buscar_atestado_por_codigo(codigo)

    col_esq, col_centro, col_dir = st.columns([1, 6, 1])
    with col_centro:
        with st.container(border=True):
            if atestado is None:
                _selo_status(
                    icone_svg=_svg("alert-triangle", 36, COR_AMBAR),
                    titulo="Atestado não encontrado",
                    cor=COR_AMBAR,
                    cor_fundo=COR_AMBAR_FUNDO,
                    subtitulo=(
                        "O código informado não corresponde a nenhum atestado em nossa base. "
                        "Confira se o QR Code foi lido corretamente ou se o link está completo."
                    ),
                )
                st.divider()
                _bloco_metadados_verificacao(codigo, rotulo_codigo="Código consultado")
            else:
                status = atestado.get("status") or "ativo"
                revogado_em = atestado.get("revogado_em")

                if status == "anonimizado":
                    # Atestado existiu e foi registrado, mas os dados pessoais
                    # (paciente, CID) foram removidos — a pedido do titular
                    # (LGPD) ou por política de retenção. Não há dado sensível
                    # para exibir aqui; só o fato de que o registro existiu.
                    _selo_status(
                        icone_svg=_svg("info", 36, COR_NEUTRA),
                        titulo="Registro existente — dados pessoais removidos",
                        cor=COR_NEUTRA,
                        cor_fundo=COR_NEUTRA_FUNDO,
                        subtitulo=(
                            "Este atestado foi emitido e registrado nesta plataforma, mas os "
                            "dados pessoais (paciente e diagnóstico) foram removidos, a pedido "
                            "do titular ou por política de retenção de dados."
                        ),
                    )
                    st.divider()
                    _bloco_metadados_verificacao(codigo)
                elif status == "revogado":
                    # _selo_status escapa `subtitulo` internamente — não escapar aqui
                    # de novo, senão o texto apareceria com entidades HTML duplicadas.
                    _selo_status(
                        icone_svg=_svg("x-circle", 36, COR_SECUNDARIA),
                        titulo="Atestado Revogado — não é mais válido",
                        cor=COR_SECUNDARIA,
                        cor_fundo="#FBEAEA",
                        subtitulo=(
                            f"Revogado pelo médico emissor em {revogado_em}."
                            if revogado_em
                            else "Este atestado foi revogado pelo médico emissor."
                        ),
                    )
                else:
                    _selo_status(
                        icone_svg=_svg("check-circle", 36, COR_PRIMARIA),
                        titulo="Atestado Autêntico",
                        cor=COR_PRIMARIA,
                        cor_fundo=COR_FUNDO_CLARO,
                    )

                if status != "anonimizado":
                    _frase_confianca()

                    st.markdown(
                        f'<p style="color:{COR_TEXTO}; font-weight:700; font-size:0.875rem; '
                        f'letter-spacing:0.04em; text-transform:uppercase; opacity:0.6; '
                        f'font-family:\'Nunito Sans\',sans-serif; margin:0 0 0.75rem 0;">'
                        f'Dados validados</p>',
                        unsafe_allow_html=True,
                    )
                    with st.container(border=True):
                        col1, col2 = st.columns(2)
                        with col1:
                            _campo_dado("Médico", atestado["nome_medico"])
                            _campo_dado("CRM", atestado["crm"])
                            _campo_dado("Data de emissão", atestado["data_emissao"])
                        with col2:
                            _campo_dado("Paciente", atestado["nome_paciente"])
                            _campo_cid_protegido()
                            _campo_dado("Período de afastamento", _formatar_periodo(atestado))

                    _bloco_metadados_verificacao(codigo)

                    st.markdown('<div class="amorsaude-nao-imprimir">', unsafe_allow_html=True)
                    st.write("")
                    _botao_imprimir()
                    st.markdown("</div>", unsafe_allow_html=True)

            _bloco_como_funciona()

    _rodape()


# ---------------------------------------------------------------------------
# TELA 2 — Login
# ---------------------------------------------------------------------------

def tela_login() -> None:
    col_esq, col_centro, col_dir = st.columns([1, 4, 1])
    with col_centro:
        with st.container(border=True):
            st.markdown(
                f'<div style="text-align:center; padding-top:0.5rem;">{_logo_html(64)}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<h2 style="text-align:center; color:{COR_PRIMARIA}; margin:0.75rem 0 0 0; '
                f'font-size:1.5rem; font-weight:800; font-family:\'Nunito Sans\',sans-serif; '
                f'letter-spacing:-0.01em;">Portal do Médico</h2>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p style="text-align:center; color:{COR_TEXTO}; opacity:0.72; '
                f'font-size:0.9375rem; margin:0.25rem 0 1rem 0; '
                f'font-family:\'Nunito Sans\',sans-serif;">'
                f'Acesso ao sistema de emissão de atestados</p>',
                unsafe_allow_html=True,
            )

            with st.form("form_login"):
                usuario = st.text_input("Usuário", placeholder="Usuário")
                senha = st.text_input("Senha", type="password")
                entrar = st.form_submit_button(
                    "Entrar", use_container_width=True, type="primary"
                )

            if entrar:
                usuario_normalizado = usuario.strip()
                if not usuario_normalizado or not senha:
                    st.warning("Preencha usuário e senha.")
                elif esta_bloqueado(usuario_normalizado):
                    st.error(
                        "Conta temporariamente bloqueada por excesso de tentativas incorretas. "
                        "Tente novamente em alguns minutos."
                    )
                else:
                    conta = autenticar(usuario_normalizado, senha)
                    if conta:
                        st.session_state["usuario"] = conta
                        st.session_state["_ultima_atividade"] = time.time()
                        st.rerun()
                    else:
                        st.error("Usuário ou senha inválidos, ou conta desativada.")

    _rodape()


# ---------------------------------------------------------------------------
# TELA 2.5 — Troca de senha obrigatória (primeiro login)
# ---------------------------------------------------------------------------

def tela_trocar_senha_obrigatoria() -> None:
    """
    Intercepta o acesso de uma conta com `deve_trocar_senha=1` — hoje isso
    acontece só com o admin inicial (ver `semear_usuarios_iniciais`), para
    garantir que a senha gerada/definida na primeira subida do app nunca
    continue em uso depois do primeiro acesso.
    """
    conta = st.session_state["usuario"]

    col_esq, col_centro, col_dir = st.columns([1, 4, 1])
    with col_centro:
        with st.container(border=True):
            st.markdown(
                f'<div style="text-align:center; padding-top:0.5rem;">{_logo_html(64)}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<h2 style="text-align:center; color:{COR_PRIMARIA}; margin:0.75rem 0 0 0; '
                f'font-size:1.5rem; font-weight:800; font-family:\'Nunito Sans\',sans-serif; '
                f'letter-spacing:-0.01em;">Troque sua senha</h2>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p style="text-align:center; color:{COR_TEXTO}; opacity:0.72; '
                f'font-size:0.9375rem; margin:0.25rem 0 1rem 0; '
                f'font-family:\'Nunito Sans\',sans-serif;">'
                f'Por segurança, defina uma nova senha antes de continuar.</p>',
                unsafe_allow_html=True,
            )

            with st.form("form_trocar_senha_obrigatoria"):
                nova_senha = st.text_input("Nova senha", type="password")
                confirmar_senha = st.text_input("Confirme a nova senha", type="password")
                salvar = st.form_submit_button(
                    "Salvar nova senha", use_container_width=True, type="primary"
                )

            if salvar:
                erro_senha = validar_senha_forte(nova_senha)
                if erro_senha:
                    st.error(erro_senha)
                elif nova_senha != confirmar_senha:
                    st.error("As senhas informadas não coincidem.")
                else:
                    redefinir_senha_usuario(conta["id"], gerar_hash_senha(nova_senha))
                    registrar_evento(
                        EVENTO_SENHA_TROCADA_PROPRIA,
                        ator_usuario=conta["usuario"],
                        ator_perfil=conta.get("perfil"),
                    )
                    conta_atualizada = dict(conta)
                    conta_atualizada["deve_trocar_senha"] = 0
                    st.session_state["usuario"] = conta_atualizada
                    st.success("Senha atualizada. Redirecionando...")
                    st.rerun()

            if st.button("Sair", use_container_width=True, type="secondary"):
                st.session_state.pop("usuario", None)
                st.rerun()

    _rodape()


# ---------------------------------------------------------------------------
# TELA 3 — Painel do administrador
# ---------------------------------------------------------------------------

def tela_admin() -> None:
    admin = st.session_state["usuario"]

    # Fail-closed: mesmo que o roteador já tenha checado o perfil antes de
    # chamar esta função, uma segunda checagem aqui garante que uma sessão
    # inconsistente/adulterada nunca renderize o painel de administrador.
    if admin.get("perfil") != "admin":
        st.session_state.pop("usuario", None)
        st.error("Sessão inválida. Faça login novamente.")
        st.stop()

    # Navegação simples entre o painel principal e as telas de auditoria e de
    # retenção/exclusão — todas vivem atrás desta mesma checagem de perfil
    # admin acima.
    if st.session_state.get("ver_auditoria"):
        tela_auditoria()
        return
    if st.session_state.get("ver_retencao"):
        tela_retencao()
        return

    conteudo_direita = (
        f'<div style="font-size:1.0625rem; font-weight:800; letter-spacing:-0.01em;">{html.escape(admin["nome"])}</div>'
        f'<div style="font-size:0.8125rem; font-weight:600; opacity:0.85; letter-spacing:0.02em;">Administrador</div>'
    )
    _barra_cabecalho(conteudo_direita)

    col_espaco, col_retencao, col_auditoria, col_sair = st.columns([2.6, 1.9, 1.6, 1])
    with col_retencao:
        if st.button("Retenção/Exclusão", use_container_width=True, type="secondary", key="ver_retencao_btn"):
            st.session_state["ver_retencao"] = True
            st.rerun()
    with col_auditoria:
        if st.button("Trilha de auditoria", use_container_width=True, type="secondary", key="ver_auditoria_btn"):
            st.session_state["ver_auditoria"] = True
            st.rerun()
    with col_sair:
        if st.button("Sair", use_container_width=True, type="secondary", key="sair_admin"):
            del st.session_state["usuario"]
            st.rerun()

    icone_cadastrar = _svg("user-plus", 17, COR_PRIMARIA, "margin-right:0.5rem; vertical-align:middle; flex-shrink:0")
    st.markdown(
        f'<h3 style="color:{COR_PRIMARIA}; margin-top:0; margin-bottom:1rem; display:flex; align-items:center; '
        f'font-family:\'Nunito Sans\',sans-serif; font-size:1.0625rem; font-weight:800; letter-spacing:-0.005em;">'
        f'{icone_cadastrar} Cadastrar médico</h3>',
        unsafe_allow_html=True,
    )

    with st.form("form_criar_medico", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        with col_a:
            nome_medico = st.text_input("Nome completo *", placeholder="ex.: Dra. Maria Souza")
            usuario_medico = st.text_input("Usuário de acesso *", placeholder="ex.: dramaria")
        with col_b:
            crm_medico = st.text_input("CRM (com UF) *", placeholder="ex.: CRM-SP 111222")
            especialidade_medico = st.text_input("Especialidade", placeholder="ex.: Clínica Geral")
        senha_inicial = st.text_input(
            "Senha inicial *",
            type="password",
            help="O médico poderá usar essa senha no primeiro acesso. A senha é guardada com hash, nunca em texto puro.",
        )
        criar = st.form_submit_button("Criar conta de médico", use_container_width=True, type="primary")

    if criar:
        erros = []
        if not nome_medico.strip():
            erros.append("Informe o nome completo do médico.")
        if not crm_medico.strip():
            erros.append("Informe o CRM (com UF).")
        if not usuario_medico.strip():
            erros.append("Informe um usuário de acesso.")
        erro_senha_inicial = validar_senha_forte(senha_inicial)
        if erro_senha_inicial:
            erros.append(erro_senha_inicial)

        if erros:
            for e in erros:
                st.error(e)
        else:
            try:
                criar_usuario(
                    usuario=usuario_medico.strip(),
                    senha_hash=gerar_hash_senha(senha_inicial),
                    nome=nome_medico.strip(),
                    perfil="medico",
                    crm=crm_medico.strip(),
                    especialidade=especialidade_medico.strip() or None,
                )
                registrar_evento(
                    EVENTO_MEDICO_CRIADO,
                    ator_usuario=admin["usuario"],
                    ator_perfil="admin",
                    origem=ORIGEM_PAINEL_ADMIN,
                    detalhe=f"medico: {usuario_medico.strip()} ({crm_medico.strip()})",
                )
                st.success(f"Conta criada para {nome_medico.strip()}.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("Esse nome de usuário já está em uso. Escolha outro.")

    st.write("")
    st.divider()

    icone_lista = _svg("list", 17, COR_PRIMARIA, "margin-right:0.5rem; vertical-align:middle; flex-shrink:0")
    st.markdown(
        f'<h3 style="color:{COR_PRIMARIA}; margin-bottom:1rem; display:flex; align-items:center; '
        f'font-family:\'Nunito Sans\',sans-serif; font-size:1.0625rem; font-weight:800; letter-spacing:-0.005em;">'
        f'{icone_lista} Médicos cadastrados</h3>',
        unsafe_allow_html=True,
    )

    medicos = listar_medicos()
    if not medicos:
        st.info("Nenhum médico cadastrado ainda.")
    else:
        for m in medicos:
            chave_reset = f"reset_senha_{m['id']}"
            with st.container(border=True):
                col_info, col_status, col_acoes = st.columns([3, 1.2, 2])
                with col_info:
                    st.markdown(
                        f'<div style="font-family:\'Nunito Sans\',sans-serif;">'
                        f'<span style="font-size:1rem; font-weight:700; color:{COR_TEXTO}; letter-spacing:-0.005em;">'
                        f'{html.escape(m["nome"])}</span><br>'
                        f'<span style="color:{COR_TEXTO}; opacity:0.6; font-size:0.8125rem; font-weight:600; letter-spacing:0.01em;">'
                        f'{html.escape(m["crm"] or "")} · usuário: <code style="font-size:0.75rem; background:{COR_FUNDO_CLARO}; '
                        f'padding:0.05em 0.3em; border-radius:4px; border:1px solid {COR_BORDA};">'
                        f'{html.escape(m["usuario"])}</code></span></div>',
                        unsafe_allow_html=True,
                    )
                with col_status:
                    if m["ativo"]:
                        st.markdown(
                            f'<span style="background:{COR_FUNDO_CLARO}; color:{COR_PRIMARIA}; '
                            f'padding:0.25rem 0.625rem; border-radius:20px; font-size:0.75rem; font-weight:700; '
                            f'font-family:\'Nunito Sans\',sans-serif; letter-spacing:0.02em;">Ativo</span>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f'<span style="background:#FBEAEA; color:{COR_SECUNDARIA}; '
                            f'padding:0.25rem 0.625rem; border-radius:20px; font-size:0.75rem; font-weight:700; '
                            f'font-family:\'Nunito Sans\',sans-serif; letter-spacing:0.02em;">Inativo</span>',
                            unsafe_allow_html=True,
                        )
                with col_acoes:
                    col_toggle, col_reset = st.columns(2)
                    with col_toggle:
                        rotulo = "Desativar" if m["ativo"] else "Ativar"
                        if st.button(rotulo, key=f"toggle_{m['id']}", use_container_width=True, type="secondary"):
                            novo_status_ativo = not m["ativo"]
                            definir_status_usuario(m["id"], novo_status_ativo)
                            registrar_evento(
                                EVENTO_MEDICO_ATIVADO if novo_status_ativo else EVENTO_MEDICO_DESATIVADO,
                                ator_usuario=admin["usuario"],
                                ator_perfil="admin",
                                origem=ORIGEM_PAINEL_ADMIN,
                                detalhe=f"medico: {m['usuario']}",
                            )
                            st.rerun()
                    with col_reset:
                        if st.button("Redefinir senha", key=f"btn_{chave_reset}", use_container_width=True, type="secondary"):
                            st.session_state[chave_reset] = True
                            st.rerun()

                if st.session_state.get(chave_reset):
                    with st.form(f"form_{chave_reset}"):
                        nova_senha = st.text_input(
                            f"Nova senha para {m['nome']}",
                            type="password",
                            key=f"nova_senha_{m['id']}",
                        )
                        col_conf, col_canc = st.columns(2)
                        with col_conf:
                            confirmar = st.form_submit_button("Confirmar", use_container_width=True, type="primary")
                        with col_canc:
                            cancelar = st.form_submit_button("Cancelar", use_container_width=True, type="secondary")
                    if confirmar:
                        erro_nova_senha = validar_senha_forte(nova_senha)
                        if erro_nova_senha:
                            st.error(erro_nova_senha)
                        else:
                            redefinir_senha_usuario(m["id"], gerar_hash_senha(nova_senha))
                            registrar_evento(
                                EVENTO_SENHA_REDEFINIDA_ADMIN,
                                ator_usuario=admin["usuario"],
                                ator_perfil="admin",
                                origem=ORIGEM_PAINEL_ADMIN,
                                detalhe=f"medico: {m['usuario']}",
                            )
                            st.session_state.pop(chave_reset, None)
                            st.success("Senha redefinida com sucesso.")
                            st.rerun()
                    if cancelar:
                        st.session_state.pop(chave_reset, None)
                        st.rerun()

                _secao_token_api(m, quem_gerencia="admin")

    st.write("")
    st.divider()
    _secao_api_integracoes()

    _rodape()


# ---------------------------------------------------------------------------
# TELA 3.5 — Trilha de auditoria (admin) — Segurança/LGPD, parte 3
# ---------------------------------------------------------------------------

_AUDITORIA_POR_PAGINA = 25


def tela_auditoria() -> None:
    """
    Lista os eventos mais recentes da trilha de auditoria, com filtro por
    período/tipo de ação e paginação simples. Só é alcançável a partir de
    `tela_admin()` (mesma checagem de perfil admin já feita lá) — repete a
    checagem aqui mesmo assim, pelo mesmo motivo "fail-closed" das outras
    telas: uma sessão inconsistente/adulterada nunca deve conseguir chegar
    aqui sem ser admin.
    """
    admin = st.session_state["usuario"]
    if admin.get("perfil") != "admin":
        st.session_state.pop("usuario", None)
        st.error("Sessão inválida. Faça login novamente.")
        st.stop()

    conteudo_direita = (
        f'<div style="font-size:1.0625rem; font-weight:800; letter-spacing:-0.01em;">{html.escape(admin["nome"])}</div>'
        f'<div style="font-size:0.8125rem; font-weight:600; opacity:0.85; letter-spacing:0.02em;">Administrador</div>'
    )
    _barra_cabecalho(conteudo_direita)

    col_espaco, col_voltar, col_sair = st.columns([4, 1.6, 1])
    with col_voltar:
        if st.button("Voltar ao painel", use_container_width=True, type="secondary", key="voltar_painel_btn"):
            st.session_state.pop("ver_auditoria", None)
            st.session_state.pop("audit_pagina", None)
            st.rerun()
    with col_sair:
        if st.button("Sair", use_container_width=True, type="secondary", key="sair_auditoria"):
            del st.session_state["usuario"]
            st.rerun()

    icone_auditoria = _svg("shield-check", 17, COR_PRIMARIA, "margin-right:0.5rem; vertical-align:middle; flex-shrink:0")
    st.markdown(
        f'<h3 style="color:{COR_PRIMARIA}; margin-top:0; margin-bottom:0.25rem; display:flex; align-items:center; '
        f'font-family:\'Nunito Sans\',sans-serif; font-size:1.0625rem; font-weight:800; letter-spacing:-0.005em;">'
        f'{icone_auditoria} Trilha de auditoria</h3>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Quem fez o quê e quando. Atestados aparecem só pelo código — nome de paciente e CID nunca ficam aqui."
    )

    col_data_ini, col_data_fim, col_tipo = st.columns([1, 1, 1.4])
    with col_data_ini:
        data_inicio = st.date_input("De", value=None, key="audit_data_inicio")
    with col_data_fim:
        data_fim = st.date_input("Até", value=None, key="audit_data_fim")
    with col_tipo:
        opcoes_tipo = ["Todos os tipos"] + TODOS_OS_TIPOS_DE_EVENTO
        tipo_selecionado = st.selectbox(
            "Tipo de ação",
            options=opcoes_tipo,
            format_func=lambda t: t if t == "Todos os tipos" else RÓTULOS_TIPOS_DE_EVENTO.get(t, t),
            key="audit_tipo",
        )

    pagina_atual = st.session_state.get("audit_pagina", 1)
    eventos, total = listar_eventos_auditoria(
        data_inicio=str(data_inicio) if data_inicio else None,
        data_fim=str(data_fim) if data_fim else None,
        tipo_evento=None if tipo_selecionado == "Todos os tipos" else tipo_selecionado,
        pagina=pagina_atual,
        por_pagina=_AUDITORIA_POR_PAGINA,
    )
    total_paginas = max(1, -(-total // _AUDITORIA_POR_PAGINA))
    if pagina_atual > total_paginas:
        pagina_atual = total_paginas
        st.session_state["audit_pagina"] = pagina_atual
        eventos, total = listar_eventos_auditoria(
            data_inicio=str(data_inicio) if data_inicio else None,
            data_fim=str(data_fim) if data_fim else None,
            tipo_evento=None if tipo_selecionado == "Todos os tipos" else tipo_selecionado,
            pagina=pagina_atual,
            por_pagina=_AUDITORIA_POR_PAGINA,
        )

    st.caption(f"{total} evento(s) encontrado(s)")

    if not eventos:
        st.info("Nenhum evento de auditoria para esse filtro.")
    else:
        linhas_tabela = [
            {
                "Data/hora": e["criado_em"],
                "Ação": RÓTULOS_TIPOS_DE_EVENTO.get(e["tipo_evento"], e["tipo_evento"]),
                "Quem": e["ator_usuario"] or "—",
                "Perfil": e["ator_perfil"] or "—",
                "Atestado (código)": (e["atestado_codigo"][:16] + "…") if e["atestado_codigo"] else "—",
                "Origem": e["origem"] or "—",
                "Detalhe": e["detalhe"] or "—",
            }
            for e in eventos
        ]
        st.dataframe(linhas_tabela, use_container_width=True, hide_index=True)

    col_prev, col_info, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("< Anterior", disabled=(pagina_atual <= 1), use_container_width=True, key="audit_prev"):
            st.session_state["audit_pagina"] = pagina_atual - 1
            st.rerun()
    with col_info:
        st.markdown(
            f'<p style="text-align:center; margin-top:0.4rem; color:{COR_TEXTO};">'
            f'Página {pagina_atual} de {total_paginas}</p>',
            unsafe_allow_html=True,
        )
    with col_next:
        if st.button("Próxima >", disabled=(pagina_atual >= total_paginas), use_container_width=True, key="audit_next"):
            st.session_state["audit_pagina"] = pagina_atual + 1
            st.rerun()

    _rodape()


# ---------------------------------------------------------------------------
# TELA 3.6 — Retenção e exclusão de dados (admin) — Segurança/LGPD, parte 4
# ---------------------------------------------------------------------------

_RÓTULOS_STATUS_ATESTADO = {
    "ativo": "Ativo",
    "revogado": "Revogado",
    "anonimizado": "Anonimizado (dados pessoais já removidos)",
}


def tela_retencao() -> None:
    """
    Ferramenta manual do admin para atender pedidos de titular (direito de
    exclusão da LGPD): localizar um atestado pelo código e ANONIMIZAR
    (remove nome/CID, mantém o registro) ou EXCLUIR definitivamente (apaga
    tudo, sem volta). Só é alcançável a partir de `tela_admin()` (mesma
    checagem de perfil admin já feita lá) — repete a checagem aqui mesmo
    assim, pelo mesmo motivo "fail-closed" das outras telas.
    """
    admin = st.session_state["usuario"]
    if admin.get("perfil") != "admin":
        st.session_state.pop("usuario", None)
        st.error("Sessão inválida. Faça login novamente.")
        st.stop()

    conteudo_direita = (
        f'<div style="font-size:1.0625rem; font-weight:800; letter-spacing:-0.01em;">{html.escape(admin["nome"])}</div>'
        f'<div style="font-size:0.8125rem; font-weight:600; opacity:0.85; letter-spacing:0.02em;">Administrador</div>'
    )
    _barra_cabecalho(conteudo_direita)

    col_espaco, col_voltar, col_sair = st.columns([4, 1.6, 1])
    with col_voltar:
        if st.button("Voltar ao painel", use_container_width=True, type="secondary", key="voltar_painel_retencao_btn"):
            st.session_state.pop("ver_retencao", None)
            st.session_state.pop("retencao_confirmar_anonimizar_codigo", None)
            st.session_state.pop("retencao_confirmar_exclusao_codigo", None)
            st.rerun()
    with col_sair:
        if st.button("Sair", use_container_width=True, type="secondary", key="sair_retencao"):
            del st.session_state["usuario"]
            st.rerun()

    icone_retencao = _svg("trash-2", 17, COR_PRIMARIA, "margin-right:0.5rem; vertical-align:middle; flex-shrink:0")
    st.markdown(
        f'<h3 style="color:{COR_PRIMARIA}; margin-top:0; margin-bottom:0.25rem; display:flex; align-items:center; '
        f'font-family:\'Nunito Sans\',sans-serif; font-size:1.0625rem; font-weight:800; letter-spacing:-0.005em;">'
        f'{icone_retencao} Retenção e exclusão de dados</h3>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Para atender pedidos de titular (direito de exclusão da LGPD). Localize um "
        "atestado pelo código e escolha anonimizar (remove nome e CID, mantém o "
        "registro) ou excluir definitivamente (apaga tudo, sem volta)."
    )

    dias_config = dias_retencao_atestados_configurados()
    if dias_config > 0:
        st.info(
            f"Retenção automática: LIGADA — atestados emitidos há mais de {dias_config} "
            f"dia(s) são anonimizados automaticamente (variável ATESTADO_RETENTION_DAYS)."
        )
    else:
        st.info(
            "Retenção automática: DESLIGADA (padrão). Nenhum atestado é anonimizado ou "
            "excluído sozinho — só por ação manual aqui nesta tela."
        )

    st.write("")
    codigo_busca = st.text_input(
        "Código do atestado",
        key="retencao_codigo_busca",
        placeholder="Cole aqui o código do atestado (o mesmo usado na URL de verificação)",
    )
    codigo_normalizado = codigo_busca.strip()

    if codigo_normalizado:
        atestado = buscar_atestado_por_codigo(codigo_normalizado)
        if not atestado:
            st.error("Nenhum atestado encontrado com esse código.")
        else:
            status_atual = atestado.get("status") or "ativo"
            with st.container(border=True):
                col1, col2 = st.columns(2)
                with col1:
                    _campo_dado("Médico", atestado["nome_medico"])
                    _campo_dado("CRM", atestado["crm"])
                    _campo_dado("Data de emissão", atestado["data_emissao"])
                with col2:
                    _campo_dado("Paciente", atestado["nome_paciente"] or "[dados já removidos]")
                    _campo_cid_protegido()
                    _campo_dado("Status atual", _RÓTULOS_STATUS_ATESTADO.get(status_atual, status_atual))

            st.write("")

            if status_atual != "anonimizado":
                if st.button(
                    "Anonimizar (remover nome e CID)",
                    key="btn_anonimizar_retencao",
                    type="secondary",
                ):
                    st.session_state["retencao_confirmar_anonimizar_codigo"] = codigo_normalizado
                    st.rerun()

                if st.session_state.get("retencao_confirmar_anonimizar_codigo") == codigo_normalizado:
                    st.warning(
                        f"Confirma a anonimização do atestado `{codigo_normalizado}`? O nome do "
                        "paciente e o CID serão removidos permanentemente — o registro (código, "
                        "datas, período, status) continua existindo. Esta ação não pode ser desfeita."
                    )
                    col_conf, col_canc = st.columns(2)
                    with col_conf:
                        if st.button(
                            "Sim, anonimizar",
                            key="confirmar_anonimizar_sim",
                            type="primary",
                            use_container_width=True,
                        ):
                            anonimizar_atestado_manual(
                                codigo_normalizado,
                                ator_usuario=admin["usuario"],
                                ator_perfil="admin",
                                origem=ORIGEM_PAINEL_ADMIN,
                            )
                            st.session_state.pop("retencao_confirmar_anonimizar_codigo", None)
                            st.success("Atestado anonimizado com sucesso.")
                            st.rerun()
                    with col_canc:
                        if st.button(
                            "Cancelar",
                            key="confirmar_anonimizar_nao",
                            type="secondary",
                            use_container_width=True,
                        ):
                            st.session_state.pop("retencao_confirmar_anonimizar_codigo", None)
                            st.rerun()
            else:
                st.info("Este atestado já está anonimizado. Só resta a opção de exclusão definitiva.")

            st.write("")
            st.divider()
            st.markdown(
                f'<p style="color:{COR_SECUNDARIA}; font-weight:800; font-family:\'Nunito Sans\',sans-serif; '
                f'font-size:0.875rem; letter-spacing:0.02em; text-transform:uppercase;">Zona de risco</p>',
                unsafe_allow_html=True,
            )
            if st.button("Excluir definitivamente", key="btn_excluir_retencao", type="secondary"):
                st.session_state["retencao_confirmar_exclusao_codigo"] = codigo_normalizado
                st.rerun()

            if st.session_state.get("retencao_confirmar_exclusao_codigo") == codigo_normalizado:
                st.error(
                    f"Isto apaga o atestado `{codigo_normalizado}` PERMANENTEMENTE do banco de "
                    "dados — sem qualquer possibilidade de recuperação, nem mesmo pelo suporte "
                    "técnico. Só a trilha de auditoria manterá o registro de que este código "
                    "existiu e foi excluído. Para confirmar, digite o código do atestado abaixo."
                )
                confirmacao_texto = st.text_input(
                    "Digite o código do atestado para confirmar a exclusão",
                    key="retencao_confirmar_exclusao_texto",
                )
                pode_confirmar = confirmacao_texto.strip() == codigo_normalizado
                col_conf2, col_canc2 = st.columns(2)
                with col_conf2:
                    if st.button(
                        "Sim, excluir definitivamente",
                        key="confirmar_exclusao_sim",
                        type="primary",
                        use_container_width=True,
                        disabled=not pode_confirmar,
                    ):
                        excluir_atestado_manual(
                            codigo_normalizado,
                            ator_usuario=admin["usuario"],
                            ator_perfil="admin",
                            origem=ORIGEM_PAINEL_ADMIN,
                        )
                        st.session_state.pop("retencao_confirmar_exclusao_codigo", None)
                        st.session_state.pop("retencao_confirmar_exclusao_texto", None)
                        st.session_state.pop("retencao_codigo_busca", None)
                        st.success("Atestado excluído definitivamente.")
                        st.rerun()
                with col_canc2:
                    if st.button(
                        "Cancelar",
                        key="confirmar_exclusao_nao",
                        type="secondary",
                        use_container_width=True,
                    ):
                        st.session_state.pop("retencao_confirmar_exclusao_codigo", None)
                        st.session_state.pop("retencao_confirmar_exclusao_texto", None)
                        st.rerun()

    _rodape()


# ---------------------------------------------------------------------------
# TELA 4 — Dashboard do médico
# ---------------------------------------------------------------------------

def tela_dashboard() -> None:
    medico = st.session_state["usuario"]

    # Fail-closed: mesmo que o roteador já tenha checado o perfil antes de
    # chamar esta função, uma segunda checagem aqui garante que uma sessão
    # inconsistente/adulterada nunca renderize o dashboard do médico.
    if medico.get("perfil") != "medico":
        st.session_state.pop("usuario", None)
        st.error("Sessão inválida. Faça login novamente.")
        st.stop()

    # Reconsulta a conta no banco para pegar mudanças feitas pelo administrador
    # nesta mesma sessão (ex.: desativação) — impede que um médico desativado
    # continue emitindo atestados enquanto a aba do navegador segue aberta.
    conta_atual = buscar_usuario_por_login(medico["usuario"])
    if not conta_atual or not conta_atual["ativo"]:
        del st.session_state["usuario"]
        st.error("Sua conta foi desativada. Procure o administrador do sistema.")
        st.stop()

    conteudo_direita = (
        f'<div style="font-size:1.0625rem; font-weight:800; letter-spacing:-0.01em;">{html.escape(medico["nome"])}</div>'
        f'<div style="font-size:0.8125rem; font-weight:600; opacity:0.85; letter-spacing:0.02em;">'
        f'{html.escape(str(medico["especialidade"] or ""))} · {html.escape(str(medico["crm"] or ""))}</div>'
    )
    _barra_cabecalho(conteudo_direita)

    col_espaco, col_sair = st.columns([5, 1])
    with col_sair:
        if st.button("Sair", use_container_width=True, type="secondary"):
            del st.session_state["usuario"]
            st.rerun()

    erro_revogacao = st.session_state.pop("erro_revogacao", None)
    if erro_revogacao:
        _caixa_mensagem(
            erro_revogacao,
            cor_fundo=COR_SECUNDARIA,
            icone=_svg("alert-triangle", 16, COR_BRANCO),
        )

    # -----------------------------------------------------------------------
    # Dados-base para os cartões e o gráfico (apenas leitura, sem alterar a fonte)
    # -----------------------------------------------------------------------
    atestados = listar_atestados_por_crm(medico["crm"])

    hoje = date.today()
    total = len(atestados)
    emitidos_este_mes = sum(
        1 for a in atestados if a["data_emissao"][:7] == hoje.strftime("%Y-%m")
    )
    emitidos_hoje = sum(1 for a in atestados if a["data_emissao"] == str(hoje))
    total_dias_afastamento = sum(a.get("dias_afastamento") or 0 for a in atestados)
    pacientes_distintos = len({a["nome_paciente"].strip().lower() for a in atestados})

    def _cartao_resumo(icone_svg: str, numero, rotulo: str) -> str:
        return f"""
        <div style="background:{COR_BRANCO}; border-top:3px solid {COR_PRIMARIA};
                    border-radius:10px; padding:1.25rem 0.75rem 1rem 0.75rem; text-align:center;
                    box-shadow:0 1px 6px rgba(95,194,212,0.10), 0 2px 12px rgba(0,0,0,0.04);
                    height:100%; font-family:'Nunito Sans',sans-serif;">
            <div style="display:flex; justify-content:center; margin-bottom:0.5rem; opacity:0.8;">{icone_svg}</div>
            <div style="font-size:2rem; font-weight:900; color:{COR_PRIMARIA}; line-height:1.1;
                        letter-spacing:-0.02em;">{numero}</div>
            <div style="color:{COR_TEXTO}; font-size:0.75rem; font-weight:600; letter-spacing:0.02em;
                        margin-top:0.375rem; opacity:0.7; line-height:1.3;">{rotulo}</div>
        </div>
        """

    icone_visao = _svg("bar-chart", 17, COR_PRIMARIA, "margin-right:0.5rem; vertical-align:middle; flex-shrink:0")
    st.markdown(
        f'<h3 style="color:{COR_PRIMARIA}; margin-top:0.5rem; margin-bottom:1rem; display:flex; align-items:center; '
        f'font-family:\'Nunito Sans\',sans-serif; font-size:1.0625rem; font-weight:800; letter-spacing:-0.005em;">'
        f'{icone_visao} Visão geral</h3>',
        unsafe_allow_html=True,
    )

    col_r1, col_r2, col_r3, col_r4, col_r5 = st.columns(5)
    with col_r1:
        st.markdown(_cartao_resumo(_svg("file-text", 24, COR_PRIMARIA), total, "Total de Atestados"), unsafe_allow_html=True)
    with col_r2:
        st.markdown(_cartao_resumo(_svg("calendar", 24, COR_PRIMARIA), emitidos_este_mes, "Emitidos este mês"), unsafe_allow_html=True)
    with col_r3:
        st.markdown(_cartao_resumo(_svg("sun", 24, COR_PRIMARIA), emitidos_hoje, "Emitidos hoje"), unsafe_allow_html=True)
    with col_r4:
        st.markdown(_cartao_resumo(_svg("bed", 24, COR_PRIMARIA), total_dias_afastamento, "Dias de afastamento concedidos"), unsafe_allow_html=True)
    with col_r5:
        st.markdown(_cartao_resumo(_svg("users", 24, COR_PRIMARIA), pacientes_distintos, "Pacientes distintos"), unsafe_allow_html=True)

    st.write("")

    # -----------------------------------------------------------------------
    # Gráfico — atestados emitidos por mês
    # -----------------------------------------------------------------------
    if atestados:
        with st.container(border=True):
            st.markdown(
                f'<p style="color:{COR_TEXTO}; font-weight:700; font-size:0.9375rem; '
                f'font-family:\'Nunito Sans\',sans-serif; margin-bottom:0.5rem; opacity:0.85;">'
                f'Atestados emitidos por mês</p>',
                unsafe_allow_html=True,
            )
            contagem_por_mes: dict[str, int] = {}
            for a in atestados:
                mes = a["data_emissao"][:7]
                contagem_por_mes[mes] = contagem_por_mes.get(mes, 0) + 1
            meses_ordenados = dict(sorted(contagem_por_mes.items()))
            st.bar_chart(meses_ordenados, color=COR_PRIMARIA, use_container_width=True)

    st.write("")
    st.divider()

    # -----------------------------------------------------------------------
    # Seção: Emitir novo atestado
    # -----------------------------------------------------------------------
    icone_emitir = _svg("file-plus", 17, COR_PRIMARIA, "margin-right:0.5rem; vertical-align:middle; flex-shrink:0")
    st.markdown(
        f'<h3 style="color:{COR_PRIMARIA}; margin-bottom:1rem; display:flex; align-items:center; '
        f'font-family:\'Nunito Sans\',sans-serif; font-size:1.0625rem; font-weight:800; letter-spacing:-0.005em;">'
        f'{icone_emitir} Emitir novo atestado</h3>',
        unsafe_allow_html=True,
    )

    with st.form("form_atestado", clear_on_submit=True):
        nome_paciente = st.text_input(
            "Nome completo do paciente *",
            placeholder="ex.: João da Silva",
        )
        cid = st.text_input(
            "CID-10 *",
            placeholder="ex.: J18.9",
            help="Código Internacional de Doenças. Usado apenas para fins de teste neste protótipo.",
        )

        col_emissao, col_modo = st.columns(2)
        with col_emissao:
            data_emissao = st.date_input(
                "Data de emissão *",
                value=date.today(),
                min_value=date(2000, 1, 1),
                max_value=date.today(),
            )
        with col_modo:
            modo_periodo = st.radio(
                "Período de afastamento",
                options=["Número de dias", "Data de início e fim"],
                horizontal=True,
            )

        if modo_periodo == "Número de dias":
            dias = st.number_input(
                "Dias de afastamento *",
                min_value=1,
                max_value=365,
                value=1,
                step=1,
            )
            data_inicio_val = None
            data_fim_val = None
        else:
            col_ini, col_fim = st.columns(2)
            with col_ini:
                data_inicio_val = st.date_input(
                    "Data de início *",
                    value=date.today(),
                    min_value=date(2000, 1, 1),
                )
            with col_fim:
                data_fim_val = st.date_input(
                    "Data de fim *",
                    value=date.today() + timedelta(days=1),
                    min_value=date(2000, 1, 1),
                )
            dias = None

        emitir = st.form_submit_button(
            "Emitir atestado e gerar QR Code", use_container_width=True, type="primary"
        )

    # Processamento do formulário (fora do bloco with form)
    if emitir:
        # Validações básicas
        erros = []
        if not nome_paciente.strip():
            erros.append("Nome do paciente é obrigatório.")
        if not cid.strip():
            erros.append("CID é obrigatório.")
        if modo_periodo == "Data de início e fim":
            if data_fim_val < data_inicio_val:
                erros.append("Data de fim não pode ser anterior à data de início.")
            else:
                dias = (data_fim_val - data_inicio_val).days + 1

        if erros:
            for e in erros:
                st.error(e)
        else:
            # Gerar código único e imprevisível
            codigo = secrets.token_urlsafe(32)

            # Persistir no banco
            try:
                salvar_atestado(
                    codigo=codigo,
                    nome_medico=medico["nome"],
                    crm=medico["crm"],
                    nome_paciente=nome_paciente.strip(),
                    cid=cid.strip().upper(),
                    data_emissao=str(data_emissao),
                    data_inicio=str(data_inicio_val) if data_inicio_val else None,
                    data_fim=str(data_fim_val) if data_fim_val else None,
                    dias_afastamento=int(dias) if dias else None,
                )
                registrar_evento(
                    EVENTO_ATESTADO_EMITIDO,
                    ator_usuario=medico["usuario"],
                    ator_perfil="medico",
                    atestado_codigo=codigo,
                    origem=ORIGEM_FORMULARIO,
                )
            except Exception as exc:
                st.error(f"Erro ao salvar atestado: {exc}. Tente novamente.")
                st.stop()

            # Gerar QR Code
            url_verificacao = f"{_url_base()}?codigo={codigo}"
            qr_bytes = gerar_qr(url_verificacao)

            st.success("Atestado emitido com sucesso.")

            # Exibir QR Code e link
            with st.container(border=True):
                col_qr, col_info = st.columns([1, 2])
                with col_qr:
                    st.image(qr_bytes, caption="QR Code de verificação", width=220)
                    st.download_button(
                        label="Baixar QR Code (PNG)",
                        data=qr_bytes,
                        file_name=f"atestado_{codigo[:12]}.png",
                        mime="image/png",
                        use_container_width=True,
                    )
                with col_info:
                    st.markdown("**Dados do atestado emitido:**")
                    st.markdown(f"- **Paciente:** {nome_paciente.strip()}")
                    st.markdown(f"- **CID:** {cid.strip().upper()}")
                    st.markdown(f"- **Emissão:** {data_emissao}")
                    if data_inicio_val and data_fim_val:
                        st.markdown(f"- **Período:** {data_inicio_val} a {data_fim_val} ({dias} dias)")
                    else:
                        st.markdown(f"- **Afastamento:** {dias} dia(s)")
                    st.markdown("**Link de verificação:**")
                    st.code(url_verificacao, language=None)

    st.divider()

    # -----------------------------------------------------------------------
    # Seção: Atestados emitidos
    # -----------------------------------------------------------------------
    icone_lista = _svg("folder-open", 17, COR_PRIMARIA, "margin-right:0.5rem; vertical-align:middle; flex-shrink:0")
    col_titulo_lista, col_export = st.columns([4, 1.4])
    with col_titulo_lista:
        st.markdown(
            f'<h3 style="color:{COR_PRIMARIA}; margin-bottom:0; display:flex; align-items:center; '
            f'font-family:\'Nunito Sans\',sans-serif; font-size:1.0625rem; font-weight:800; letter-spacing:-0.005em;">'
            f'{icone_lista} Atestados emitidos por você</h3>',
            unsafe_allow_html=True,
        )
    with col_export:
        if atestados:
            st.write("")
            st.download_button(
                "Exportar CSV",
                data=_gerar_csv(atestados),
                file_name=f"atestados_{medico['crm'].replace(' ', '_')}.csv",
                mime="text/csv",
                use_container_width=True,
                type="secondary",
            )

    busca = st.text_input(
        "Buscar por nome do paciente",
        placeholder="Digite o nome do paciente para filtrar…",
    )

    atestados_filtrados = atestados
    if busca.strip():
        termo = busca.strip().lower()
        atestados_filtrados = [a for a in atestados if a["nome_paciente"] and termo in a["nome_paciente"].lower()]

    if not atestados:
        st.info("Nenhum atestado emitido ainda. Use o formulário acima para criar o primeiro.")
    elif not atestados_filtrados:
        st.info(f"Nenhum atestado encontrado para \"{busca.strip()}\".")
    else:
        st.caption(f"{len(atestados_filtrados)} de {len(atestados)} atestado(s)")

        for a in atestados_filtrados:
            codigo_atestado = a["codigo"]
            status_atestado = a.get("status") or "ativo"
            chave_confirmacao = f"confirmar_revogar_{codigo_atestado}"

            with st.container(border=True):
                col_a, col_b = st.columns([3, 1.2])
                with col_a:
                    st.markdown(
                        f'<div style="font-family:\'Nunito Sans\',sans-serif;">'
                        f'<span style="font-size:1rem; font-weight:700; color:{COR_TEXTO}; letter-spacing:-0.005em;">'
                        f'{html.escape(a["nome_paciente"] or "[dados removidos]")}</span></div>',
                        unsafe_allow_html=True,
                    )
                with col_b:
                    if status_atestado == "anonimizado":
                        st.markdown(
                            f'<span style="background:{COR_NEUTRA_FUNDO}; color:{COR_NEUTRA}; '
                            f'padding:0.25rem 0.625rem; border-radius:20px; font-size:0.75rem; font-weight:700; '
                            f'font-family:\'Nunito Sans\',sans-serif; letter-spacing:0.02em;">'
                            f'Dados removidos</span>',
                            unsafe_allow_html=True,
                        )
                    elif status_atestado == "revogado":
                        st.markdown(
                            f'<span style="background:#FBEAEA; color:{COR_SECUNDARIA}; '
                            f'padding:0.25rem 0.625rem; border-radius:20px; font-size:0.75rem; font-weight:700; '
                            f'font-family:\'Nunito Sans\',sans-serif; letter-spacing:0.02em;">'
                            f'Revogado</span>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f'<span style="background:{COR_FUNDO_CLARO}; color:{COR_PRIMARIA}; '
                            f'padding:0.25rem 0.625rem; border-radius:20px; font-size:0.75rem; font-weight:700; '
                            f'font-family:\'Nunito Sans\',sans-serif; letter-spacing:0.02em;">Ativo</span>',
                            unsafe_allow_html=True,
                        )

                col_1, col_2, col_3, col_4 = st.columns(4)
                col_1.markdown(f"**CID**  \n{a['cid'] or '—'}")
                col_2.markdown(f"**Emissão**  \n{a['data_emissao']}")
                col_3.markdown(f"**Período**  \n{_formatar_periodo(a)}")
                col_4.markdown(f"**Código**  \n`{codigo_atestado[:8]}…`")

                chave_toggle = f"mostrar_qr_{codigo_atestado}"
                url = f"{_url_base()}?codigo={codigo_atestado}"

                if status_atestado == "anonimizado":
                    col_btn1, col_btn2 = st.columns(2)
                    with col_btn1:
                        rotulo_qr = "Ocultar QR" if st.session_state.get(chave_toggle) else "Ver QR"
                        if st.button(rotulo_qr, key=f"btn_qr_{codigo_atestado}", use_container_width=True, type="secondary"):
                            st.session_state[chave_toggle] = not st.session_state.get(chave_toggle, False)
                    with col_btn2:
                        _botao_copiar_link(url, chave=codigo_atestado)
                    icone_info = _svg("info", 13, COR_NEUTRA, "margin-right:0.3rem; vertical-align:middle")
                    st.markdown(
                        f'<p style="color:{COR_NEUTRA}; font-size:0.82rem; font-weight:600; margin-top:0.5rem;">'
                        f'{icone_info} Dados pessoais removidos (anonimizado)</p>',
                        unsafe_allow_html=True,
                    )
                elif status_atestado == "revogado":
                    col_btn1, col_btn2 = st.columns(2)
                    with col_btn1:
                        rotulo_qr = "Ocultar QR" if st.session_state.get(chave_toggle) else "Ver QR"
                        if st.button(rotulo_qr, key=f"btn_qr_{codigo_atestado}", use_container_width=True, type="secondary"):
                            st.session_state[chave_toggle] = not st.session_state.get(chave_toggle, False)
                    with col_btn2:
                        _botao_copiar_link(url, chave=codigo_atestado)
                    icone_ban = _svg("ban", 13, COR_SECUNDARIA, "margin-right:0.3rem; vertical-align:middle")
                    st.markdown(
                        f'<p style="color:{COR_SECUNDARIA}; font-size:0.82rem; font-weight:600; margin-top:0.5rem;">'
                        f'{icone_ban} Revogado em {html.escape(str(a.get("revogado_em") or ""))}</p>',
                        unsafe_allow_html=True,
                    )
                elif st.session_state.get(chave_confirmacao):
                    st.warning(
                        "Tem certeza que deseja revogar este atestado? Esta ação não pode ser desfeita.",
                    )
                    col_conf1, col_conf2 = st.columns(2)
                    with col_conf1:
                        if st.button(
                            "Sim, revogar atestado",
                            key=f"confirmar_sim_{codigo_atestado}",
                            use_container_width=True,
                            type="primary",
                        ):
                            sucesso = revogar_atestado(codigo_atestado, medico["crm"])
                            st.session_state.pop(chave_confirmacao, None)
                            if not sucesso:
                                st.session_state["erro_revogacao"] = (
                                    "Não foi possível revogar este atestado — "
                                    "ele já pode ter sido revogado nesse meio tempo."
                                )
                            else:
                                registrar_evento(
                                    EVENTO_ATESTADO_REVOGADO,
                                    ator_usuario=medico["usuario"],
                                    ator_perfil="medico",
                                    atestado_codigo=codigo_atestado,
                                    origem=ORIGEM_FORMULARIO,
                                )
                            st.rerun()
                    with col_conf2:
                        if st.button(
                            "Cancelar",
                            key=f"confirmar_nao_{codigo_atestado}",
                            use_container_width=True,
                            type="secondary",
                        ):
                            st.session_state.pop(chave_confirmacao, None)
                            st.rerun()
                else:
                    col_btn1, col_btn2, col_btn3 = st.columns(3)
                    with col_btn1:
                        rotulo_qr = "Ocultar QR" if st.session_state.get(chave_toggle) else "Ver QR"
                        if st.button(rotulo_qr, key=f"btn_qr_{codigo_atestado}", use_container_width=True, type="secondary"):
                            st.session_state[chave_toggle] = not st.session_state.get(chave_toggle, False)
                    with col_btn2:
                        _botao_copiar_link(url, chave=codigo_atestado)
                    with col_btn3:
                        if st.button(
                            "Revogar atestado",
                            key=f"revogar_{codigo_atestado}",
                            use_container_width=True,
                            type="primary",
                        ):
                            st.session_state[chave_confirmacao] = True
                            st.rerun()

                if st.session_state.get(chave_toggle):
                    qr_mini = gerar_qr(url, tamanho_caixa=6, borda=2)
                    col_vazia1, col_qr_meio, col_vazia2 = st.columns([1, 1, 1])
                    with col_qr_meio:
                        st.image(qr_mini, caption=f"QR — {a['nome_paciente'] or codigo_atestado}", use_container_width=True)

    st.write("")
    st.divider()

    icone_api = _svg("plug", 17, COR_PRIMARIA, "margin-right:0.5rem; vertical-align:middle; flex-shrink:0")
    st.markdown(
        f'<h3 style="color:{COR_PRIMARIA}; margin-bottom:1rem; display:flex; align-items:center; '
        f'font-family:\'Nunito Sans\',sans-serif; font-size:1.0625rem; font-weight:800; letter-spacing:-0.005em;">'
        f'{icone_api} Registro automático (API)</h3>',
        unsafe_allow_html=True,
    )
    _secao_token_api(conta_atual, quem_gerencia="medico")
    _secao_api_integracoes()

    _rodape()


def _rodape() -> None:
    st.markdown(
        f"""
        <div style="text-align:center; color:{COR_TEXTO}; opacity:0.5;
                    font-size:0.75rem; font-weight:600; letter-spacing:0.04em;
                    font-family:'Nunito Sans',sans-serif;
                    padding:2rem 0 1rem 0;">
            AmorSaúde — Validador de Atestados
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Roteador principal
# ---------------------------------------------------------------------------

# Auto-logout por inatividade: cada tela autenticada atualiza
# "_ultima_atividade" a cada interação (qualquer clique/submit dispara um
# rerun do Streamlit); se o tempo desde a última interação passar do limite,
# a sessão é encerrada na próxima interação do usuário. Não é um timer ativo
# em segundo plano (o Streamlit não roda código enquanto a aba fica parada) —
# na prática, expira "na próxima ação" após o tempo de inatividade, o que já
# cobre o objetivo de não deixar uma sessão esquecida utilizável indefinidamente.
_TIMEOUT_INATIVIDADE_SEGUNDOS = 30 * 60  # 30 minutos

codigo_url = st.query_params.get("codigo")

if codigo_url:
    tela_verificacao(str(codigo_url))
elif "usuario" not in st.session_state:
    tela_login()
elif (time.time() - st.session_state.get("_ultima_atividade", time.time())) > _TIMEOUT_INATIVIDADE_SEGUNDOS:
    st.session_state.clear()
    st.warning("Sessão expirada por inatividade. Faça login novamente.")
    tela_login()
else:
    st.session_state["_ultima_atividade"] = time.time()
    if st.session_state["usuario"].get("deve_trocar_senha"):
        tela_trocar_senha_obrigatoria()
    elif st.session_state["usuario"]["perfil"] == "admin":
        tela_admin()
    else:
        tela_dashboard()
