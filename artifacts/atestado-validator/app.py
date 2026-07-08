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
from datetime import date, timedelta
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from src.auth import MEDICOS_TESTE, autenticar
from src.database import (
    buscar_atestado_por_codigo,
    init_db,
    listar_atestados_por_crm,
    revogar_atestado,
    salvar_atestado,
)
from src.qr_generator import gerar_qr

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

_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo-amorsaude.png"

# ---------------------------------------------------------------------------
# Configuração da página
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AmorSaúde — Atestados",
    page_icon="🩺",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Inicialização do banco (idempotente)
# ---------------------------------------------------------------------------
init_db()


# ---------------------------------------------------------------------------
# Identidade visual — CSS global + helpers de marca
# ---------------------------------------------------------------------------

def _injetar_estilo() -> None:
    st.markdown(
        f"""
        <style>
        /* Fundo geral da página */
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
        h1, h2, h3, h4, p, span, label, .stMarkdown, .stCaption {{
            color: {COR_TEXTO};
        }}

        /* Cards com borda (st.container(border=True)) — fundo branco, sombra suave */
        [data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 14px !important;
            box-shadow: 0 2px 14px rgba(95, 194, 212, 0.15) !important;
            background-color: {COR_BRANCO} !important;
            border-color: {COR_BORDA} !important;
        }}

        /* Formulários — fundo branco, nunca escuro */
        [data-testid="stForm"] {{
            background-color: {COR_BRANCO} !important;
            border-radius: 14px !important;
            padding: 1.5rem !important;
            border: 1px solid {COR_BORDA} !important;
        }}

        /* Campos de texto, número, data, seleção, textarea — fundo branco + texto escuro legível */
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input,
        [data-testid="stTextArea"] textarea,
        [data-baseweb="input"],
        [data-baseweb="select"] > div {{
            background-color: {COR_BRANCO} !important;
            color: {COR_TEXTO} !important;
            border: 1px solid {COR_BORDA} !important;
        }}
        [data-testid="stTextInput"], [data-testid="stNumberInput"],
        [data-testid="stDateInput"], [data-testid="stTextArea"] {{
            background-color: transparent !important;
        }}

        /* Expander (credenciais de teste) */
        [data-testid="stExpander"] {{
            background-color: {COR_BRANCO} !important;
            border: 1px solid {COR_BORDA} !important;
            border-radius: 10px !important;
        }}
        [data-testid="stExpander"] summary {{
            color: {COR_PRIMARIA} !important;
        }}

        /* Botões primários (CTAs) — vermelho AmorSaúde */
        button[kind="primary"] {{
            background-color: {COR_CTA} !important;
            border-color: {COR_CTA} !important;
            color: {COR_BRANCO} !important;
        }}
        button[kind="primary"]:hover {{
            background-color: #b8241c !important;
            border-color: #b8241c !important;
        }}
        /* Botões secundários — contorno verde-água, fundo branco */
        button[kind="secondary"] {{
            background-color: {COR_BRANCO} !important;
            border-color: {COR_PRIMARIA} !important;
            color: {COR_PRIMARIA} !important;
        }}
        button[kind="secondary"]:hover {{
            border-color: {COR_CTA} !important;
            color: {COR_CTA} !important;
        }}

        [data-testid="stMetricValue"] {{
            color: {COR_PRIMARIA} !important;
        }}
        [data-testid="stMetricLabel"] {{
            color: {COR_TEXTO} !important;
        }}

        hr {{
            border-color: {COR_BORDA} !important;
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
        f'color:{cor_fallback}; font-family:sans-serif;">AmorSaúde</span>'
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
        f'<div style="background-color:{COR_PRIMARIA}; padding:1.3rem 1.8rem; '
        f'border-radius:14px; display:flex; align-items:center; '
        f'justify-content:space-between; margin-bottom:1.8rem; gap:1rem; '
        f'box-shadow:0 2px 10px rgba(0,0,0,0.08);">'
        f'<div style="background-color:{COR_BRANCO}; border-radius:10px; '
        f'padding:0.45rem 0.9rem; display:flex; align-items:center; '
        f'min-width:0; flex-shrink:0;">'
        f'{_logo_html(40, cor_fallback=COR_PRIMARIA)}'
        f'</div>'
        f'<div style="color:{COR_BRANCO}; text-align:right;">{conteudo_direita}</div>'
        f'</div>'
    )
    st.markdown(html_str, unsafe_allow_html=True)


def _caixa_mensagem(texto: str, cor_fundo: str, cor_texto: str = COR_BRANCO, icone: str = "") -> None:
    """Caixa de mensagem customizada (usada para o estado de atestado inválido em coral)."""
    st.markdown(
        f"""
        <div style="background-color:{cor_fundo}; color:{cor_texto}; padding:1rem 1.2rem;
                    border-radius:10px; font-weight:600; margin:0.6rem 0;">
            {icone} {texto}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _rodape() -> None:
    st.markdown(
        f"""
        <div style="text-align:center; color:{COR_TEXTO}; opacity:0.6;
                    font-size:0.8rem; padding:1.5rem 0 0.5rem 0;">
            AmorSaúde — Validador de Atestados
        </div>
        """,
        unsafe_allow_html=True,
    )


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
    html_conteudo = f"""
    <button id="{id_seguro}" data-url="{url_escapada}"
            style="background-color:{COR_BRANCO}; color:{COR_PRIMARIA};
                   border:1px solid {COR_PRIMARIA}; border-radius:6px;
                   padding:0.42rem 0.6rem; cursor:pointer; font-size:0.82rem;
                   width:100%; font-family:sans-serif;">
        📋 Copiar link
    </button>
    <script>
        (function() {{
            var btn = document.getElementById("{id_seguro}");
            btn.addEventListener("click", function() {{
                var url = btn.getAttribute("data-url");
                navigator.clipboard.writeText(url);
                btn.innerText = "✅ Copiado!";
                setTimeout(function() {{ btn.innerText = "📋 Copiar link"; }}, 1500);
            }});
        }})();
    </script>
    """
    components.html(html_conteudo, height=42)


_injetar_estilo()

# ---------------------------------------------------------------------------
# Helpers de negócio (inalterados)
# ---------------------------------------------------------------------------

def _url_base() -> str:
    """Monta a URL base do app a partir da variável de ambiente do Replit."""
    dominio = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if dominio:
        return f"https://{dominio}/"
    # Fallback para ambiente local
    return "http://localhost:5000/"


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

    if atestado is None:
        st.markdown(
            f'<h2 style="color:{COR_SECUNDARIA};">Verificação de Atestado</h2>',
            unsafe_allow_html=True,
        )
        _caixa_mensagem(
            "Atestado não encontrado ou código inválido.",
            cor_fundo=COR_SECUNDARIA,
            icone="❌",
        )
        st.markdown(
            "O código informado não corresponde a nenhum atestado em nossa base. "
            "Verifique se o QR Code foi lido corretamente e tente novamente."
        )
        _rodape()
        return

    status = atestado.get("status") or "ativo"

    if status == "revogado":
        st.markdown(
            f'<h2 style="color:{COR_SECUNDARIA};">🚫 Atestado REVOGADO — não é mais válido</h2>',
            unsafe_allow_html=True,
        )
        revogado_em = atestado.get("revogado_em")
        _caixa_mensagem(
            f"Este atestado foi revogado pelo médico emissor{f' em {revogado_em}' if revogado_em else ''} "
            "e não deve mais ser aceito como comprovante válido.",
            cor_fundo=COR_SECUNDARIA,
            icone="⚠️",
        )
    else:
        st.markdown(
            f'<h2 style="color:{COR_PRIMARIA};">✅ Atestado Autêntico</h2>',
            unsafe_allow_html=True,
        )
    st.caption("Consulta pública — nenhum dado pessoal seu é registrado nesta verificação.")

    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Médico", atestado["nome_medico"])
            st.metric("CRM", atestado["crm"])
            st.metric("Data de emissão", atestado["data_emissao"])
        with col2:
            st.metric("Paciente", atestado["nome_paciente"])
            st.metric("CID", atestado["cid"])
            st.metric("Período de afastamento", _formatar_periodo(atestado))

    st.caption(f"Código do atestado: `{codigo}`")
    _rodape()


# ---------------------------------------------------------------------------
# TELA 2 — Login
# ---------------------------------------------------------------------------

def tela_login() -> None:
    col_esq, col_centro, col_dir = st.columns([1, 2, 1])
    with col_centro:
        with st.container(border=True):
            st.markdown(
                f'<div style="text-align:center; padding-top:0.5rem;">{_logo_html(64)}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<h2 style="text-align:center; color:{COR_PRIMARIA}; margin-bottom:0;">Portal do Médico</h2>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p style="text-align:center; color:{COR_TEXTO};">Acesso ao sistema de emissão de atestados</p>',
                unsafe_allow_html=True,
            )

            with st.expander("🔑 Credenciais de teste (clique para ver)", expanded=True):
                st.markdown("**Contas disponíveis para teste:**")
                colunas = st.columns(len(MEDICOS_TESTE))
                for col, m in zip(colunas, MEDICOS_TESTE):
                    with col:
                        st.markdown(
                            f"**{m['nome']}**  \n"
                            f"Usuário: `{m['usuario']}`  \n"
                            f"Senha: `{m['senha']}`  \n"
                            f"CRM: {m['crm']}"
                        )

            with st.form("form_login"):
                usuario = st.text_input("Usuário", placeholder="ex.: drsilva")
                senha = st.text_input("Senha", type="password")
                entrar = st.form_submit_button(
                    "Entrar", use_container_width=True, type="primary"
                )

            if entrar:
                if not usuario or not senha:
                    st.warning("Preencha usuário e senha.")
                else:
                    medico = autenticar(usuario.strip(), senha)
                    if medico:
                        st.session_state["medico"] = medico
                        st.rerun()
                    else:
                        st.error("Usuário ou senha inválidos. Verifique as credenciais de teste acima.")

    _rodape()


# ---------------------------------------------------------------------------
# TELA 3 — Dashboard do médico
# ---------------------------------------------------------------------------

def tela_dashboard() -> None:
    medico = st.session_state["medico"]

    conteudo_direita = (
        f'<div style="font-size:1.2rem; font-weight:700;">{medico["nome"]}</div>'
        f'<div style="font-size:0.88rem; opacity:0.92;">{medico["especialidade"]} · {medico["crm"]}</div>'
    )
    _barra_cabecalho(conteudo_direita)

    col_espaco, col_sair = st.columns([5, 1])
    with col_sair:
        if st.button("Sair", use_container_width=True, type="secondary"):
            del st.session_state["medico"]
            st.rerun()

    erro_revogacao = st.session_state.pop("erro_revogacao", None)
    if erro_revogacao:
        _caixa_mensagem(erro_revogacao, cor_fundo=COR_SECUNDARIA, icone="⚠️")

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

    def _cartao_resumo(icone: str, numero, rotulo: str) -> str:
        return f"""
        <div style="background:{COR_BRANCO}; border-top:4px solid {COR_PRIMARIA};
                    border-radius:12px; padding:1.1rem 0.8rem; text-align:center;
                    box-shadow:0 2px 10px rgba(0,0,0,0.06); height:100%;">
            <div style="font-size:1.4rem;">{icone}</div>
            <div style="font-size:1.9rem; font-weight:800; color:{COR_PRIMARIA}; line-height:1.2;">{numero}</div>
            <div style="color:{COR_TEXTO}; font-size:0.8rem; margin-top:0.15rem;">{rotulo}</div>
        </div>
        """

    st.markdown(
        f'<h3 style="color:{COR_PRIMARIA}; margin-top:0.5rem;">📊 Visão geral</h3>',
        unsafe_allow_html=True,
    )

    col_r1, col_r2, col_r3, col_r4, col_r5 = st.columns(5)
    with col_r1:
        st.markdown(_cartao_resumo("📄", total, "Total de Atestados"), unsafe_allow_html=True)
    with col_r2:
        st.markdown(_cartao_resumo("📅", emitidos_este_mes, "Emitidos este mês"), unsafe_allow_html=True)
    with col_r3:
        st.markdown(_cartao_resumo("☀️", emitidos_hoje, "Emitidos hoje"), unsafe_allow_html=True)
    with col_r4:
        st.markdown(_cartao_resumo("🛌", total_dias_afastamento, "Dias de afastamento concedidos"), unsafe_allow_html=True)
    with col_r5:
        st.markdown(_cartao_resumo("👥", pacientes_distintos, "Pacientes distintos"), unsafe_allow_html=True)

    st.write("")

    # -----------------------------------------------------------------------
    # Gráfico — atestados emitidos por mês
    # -----------------------------------------------------------------------
    if atestados:
        with st.container(border=True):
            st.markdown(
                f'<p style="color:{COR_TEXTO}; font-weight:600; margin-bottom:0.4rem;">Atestados emitidos por mês</p>',
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
    st.markdown(f'<h3 style="color:{COR_PRIMARIA};">📋 Emitir novo atestado</h3>', unsafe_allow_html=True)

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
            "✅ Emitir atestado e gerar QR Code", use_container_width=True, type="primary"
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
            except Exception as exc:
                st.error(f"Erro ao salvar atestado: {exc}. Tente novamente.")
                st.stop()

            # Gerar QR Code
            url_verificacao = f"{_url_base()}?codigo={codigo}"
            qr_bytes = gerar_qr(url_verificacao)

            st.success("✅ Atestado emitido com sucesso!")

            # Exibir QR Code e link
            with st.container(border=True):
                col_qr, col_info = st.columns([1, 2])
                with col_qr:
                    st.image(qr_bytes, caption="QR Code de verificação", width=220)
                    st.download_button(
                        label="⬇️ Baixar QR Code (PNG)",
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
    col_titulo_lista, col_export = st.columns([4, 1.4])
    with col_titulo_lista:
        st.markdown(f'<h3 style="color:{COR_PRIMARIA};">📁 Atestados emitidos por você</h3>', unsafe_allow_html=True)
    with col_export:
        if atestados:
            st.write("")
            st.download_button(
                "⬇️ Exportar CSV",
                data=_gerar_csv(atestados),
                file_name=f"atestados_{medico['crm'].replace(' ', '_')}.csv",
                mime="text/csv",
                use_container_width=True,
                type="secondary",
            )

    busca = st.text_input(
        "🔍 Buscar por nome do paciente",
        placeholder="Digite o nome do paciente para filtrar…",
    )

    atestados_filtrados = atestados
    if busca.strip():
        termo = busca.strip().lower()
        atestados_filtrados = [a for a in atestados if termo in a["nome_paciente"].lower()]

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
                        f'<span style="font-size:1.05rem; font-weight:700; color:{COR_TEXTO};">'
                        f'{html.escape(a["nome_paciente"])}</span>',
                        unsafe_allow_html=True,
                    )
                with col_b:
                    if status_atestado == "revogado":
                        st.markdown(
                            f'<span style="background:#FBEAEA; color:{COR_SECUNDARIA}; '
                            f'padding:0.2rem 0.6rem; border-radius:20px; font-size:0.78rem; font-weight:700;">'
                            f'● Revogado</span>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f'<span style="background:{COR_FUNDO_CLARO}; color:{COR_PRIMARIA}; '
                            f'padding:0.2rem 0.6rem; border-radius:20px; font-size:0.78rem; font-weight:700;">● Ativo</span>',
                            unsafe_allow_html=True,
                        )

                col_1, col_2, col_3, col_4 = st.columns(4)
                col_1.markdown(f"**CID**  \n{a['cid']}")
                col_2.markdown(f"**Emissão**  \n{a['data_emissao']}")
                col_3.markdown(f"**Período**  \n{_formatar_periodo(a)}")
                col_4.markdown(f"**Código**  \n`{codigo_atestado[:8]}…`")

                chave_toggle = f"mostrar_qr_{codigo_atestado}"
                url = f"{_url_base()}?codigo={codigo_atestado}"

                if status_atestado == "revogado":
                    col_btn1, col_btn2 = st.columns(2)
                    with col_btn1:
                        rotulo_qr = "Ocultar QR" if st.session_state.get(chave_toggle) else "🔳 Ver QR"
                        if st.button(rotulo_qr, key=f"btn_qr_{codigo_atestado}", use_container_width=True, type="secondary"):
                            st.session_state[chave_toggle] = not st.session_state.get(chave_toggle, False)
                    with col_btn2:
                        _botao_copiar_link(url, chave=codigo_atestado)
                    st.markdown(
                        f'<p style="color:{COR_SECUNDARIA}; font-size:0.82rem; font-weight:600; margin-top:0.5rem;">'
                        f'🚫 Revogado em {html.escape(str(a.get("revogado_em") or ""))}</p>',
                        unsafe_allow_html=True,
                    )
                elif st.session_state.get(chave_confirmacao):
                    st.warning(
                        "⚠️ Tem certeza que deseja revogar este atestado? Esta ação não pode ser desfeita.",
                        icon="⚠️",
                    )
                    col_conf1, col_conf2 = st.columns(2)
                    with col_conf1:
                        if st.button(
                            "✅ Sim, revogar atestado",
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
                        rotulo_qr = "Ocultar QR" if st.session_state.get(chave_toggle) else "🔳 Ver QR"
                        if st.button(rotulo_qr, key=f"btn_qr_{codigo_atestado}", use_container_width=True, type="secondary"):
                            st.session_state[chave_toggle] = not st.session_state.get(chave_toggle, False)
                    with col_btn2:
                        _botao_copiar_link(url, chave=codigo_atestado)
                    with col_btn3:
                        if st.button(
                            "🚫 Revogar atestado",
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
                        st.image(qr_mini, caption=f"QR — {a['nome_paciente']}", use_container_width=True)

    _rodape()


# ---------------------------------------------------------------------------
# Roteador principal
# ---------------------------------------------------------------------------

codigo_url = st.query_params.get("codigo")

if codigo_url:
    tela_verificacao(str(codigo_url))
elif "medico" not in st.session_state:
    tela_login()
else:
    tela_dashboard()
