"""
Validador de Atestados — ponto de entrada principal do app Streamlit.

Fase 1 (esqueleto): apenas título e upload de arquivo.
A lógica de validação será adicionada nas fases seguintes.
"""

import streamlit as st

st.set_page_config(
    page_title="Validador de Atestados",
    page_icon="🔍",
    layout="centered",
)

st.title("Validador de Atestados")
st.caption(
    "Ferramenta de **apoio** à análise de atestados médicos, odontológicos e de comparecimento. "
    "O sistema **não emite veredito final** — a decisão cabe sempre ao analista humano (RH/Auditoria)."
)

st.divider()

uploaded_file = st.file_uploader(
    label="Envie o atestado para análise",
    type=["pdf", "png", "jpg", "jpeg", "tiff", "bmp"],
    help="Formatos aceitos: PDF e imagens (PNG, JPG, TIFF, BMP). Não envie documentos com dados pessoais reais em ambiente de teste.",
)

if uploaded_file is not None:
    st.success(f"Arquivo recebido: **{uploaded_file.name}** ({uploaded_file.type})")
    st.info("⚙️ Lógica de validação ainda não implementada — em desenvolvimento nas próximas fases.")
