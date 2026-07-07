"""
Validador de Atestados — app principal Streamlit.

Roteamento de telas:
  ?codigo=XXX  → Página pública de verificação (sem login)
  (sem código) → Login → Dashboard do médico
"""

import os
import secrets
from datetime import date, timedelta

import streamlit as st

from src.auth import MEDICOS_TESTE, autenticar
from src.database import (
    buscar_atestado_por_codigo,
    init_db,
    listar_atestados_por_crm,
    salvar_atestado,
)
from src.qr_generator import gerar_qr

# ---------------------------------------------------------------------------
# Configuração da página
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Atestados Médicos",
    page_icon="🩺",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Inicialização do banco (idempotente)
# ---------------------------------------------------------------------------
init_db()

# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# TELA 1 — Verificação pública (?codigo=XXX)
# ---------------------------------------------------------------------------

def tela_verificacao(codigo: str) -> None:
    st.title("🔍 Verificação de Atestado")
    st.caption("Consulta pública — nenhum dado pessoal seu é registrado nesta verificação.")
    st.divider()

    with st.spinner("Consultando banco de dados…"):
        atestado = buscar_atestado_por_codigo(codigo)

    if atestado is None:
        st.error("❌ Atestado não encontrado ou código inválido.")
        st.markdown(
            "O código informado não corresponde a nenhum atestado em nossa base. "
            "Verifique se o QR Code foi lido corretamente e tente novamente."
        )
        return

    st.success("✅ Atestado autêntico")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Médico", atestado["nome_medico"])
        st.metric("CRM", atestado["crm"])
        st.metric("Data de emissão", atestado["data_emissao"])
    with col2:
        st.metric("Paciente", atestado["nome_paciente"])
        st.metric("CID", atestado["cid"])
        st.metric("Período de afastamento", _formatar_periodo(atestado))

    st.divider()
    st.caption(f"Código do atestado: `{codigo}`")


# ---------------------------------------------------------------------------
# TELA 2 — Login
# ---------------------------------------------------------------------------

def tela_login() -> None:
    st.title("🩺 Portal do Médico")
    st.subheader("Acesso ao sistema de emissão de atestados")
    st.divider()

    # Credenciais de teste visíveis para facilitar os testes
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

    st.divider()

    with st.form("form_login"):
        usuario = st.text_input("Usuário", placeholder="ex.: drsilva")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar", use_container_width=True)

    if entrar:
        if not usuario or not senha:
            st.warning("Preencha usuário e senha.")
            return
        medico = autenticar(usuario.strip(), senha)
        if medico:
            st.session_state["medico"] = medico
            st.rerun()
        else:
            st.error("Usuário ou senha inválidos. Verifique as credenciais de teste acima.")


# ---------------------------------------------------------------------------
# TELA 3 — Dashboard do médico
# ---------------------------------------------------------------------------

def tela_dashboard() -> None:
    medico = st.session_state["medico"]

    # Cabeçalho com botão de sair
    col_titulo, col_sair = st.columns([5, 1])
    with col_titulo:
        st.title(f"🩺 Olá, {medico['nome']}")
        st.caption(f"{medico['especialidade']} · {medico['crm']}")
    with col_sair:
        st.write("")
        st.write("")
        if st.button("Sair", use_container_width=True):
            del st.session_state["medico"]
            st.rerun()

    st.divider()

    # -----------------------------------------------------------------------
    # Seção: Emitir novo atestado
    # -----------------------------------------------------------------------
    st.subheader("📋 Emitir novo atestado")

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

        emitir = st.form_submit_button("✅ Emitir atestado e gerar QR Code", use_container_width=True)

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
    st.subheader("📁 Atestados emitidos por você")

    atestados = listar_atestados_por_crm(medico["crm"])

    if not atestados:
        st.info("Nenhum atestado emitido ainda. Use o formulário acima para criar o primeiro.")
    else:
        st.caption(f"{len(atestados)} atestado(s) encontrado(s)")

        for a in atestados:
            with st.container(border=True):
                col_a, col_b, col_c = st.columns([3, 2, 2])
                with col_a:
                    st.markdown(f"**Paciente:** {a['nome_paciente']}")
                    st.markdown(f"**CID:** {a['cid']}")
                with col_b:
                    st.markdown(f"**Emissão:** {a['data_emissao']}")
                    st.markdown(f"**Período:** {_formatar_periodo(a)}")
                with col_c:
                    url = f"{_url_base()}?codigo={a['codigo']}"
                    st.markdown(f"**Código:** `{a['codigo'][:12]}…`")
                    # Mini QR
                    qr_mini = gerar_qr(url, tamanho_caixa=4, borda=2)
                    st.image(qr_mini, width=80)


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
