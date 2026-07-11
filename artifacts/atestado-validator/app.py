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
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from src.auth import ADMIN_INICIAL, MEDICOS_TESTE, autenticar, gerar_hash_senha, semear_usuarios_iniciais
from src.database import (
    buscar_atestado_por_codigo,
    buscar_usuario_por_login,
    contar_oauth_access_tokens_ativos,
    criar_usuario,
    definir_status_usuario,
    init_db,
    listar_atestados_por_crm,
    listar_medicos,
    redefinir_senha_usuario,
    revogar_atestado,
    revogar_oauth_access_tokens,
    revogar_token_api,
    salvar_atestado,
    salvar_token_api,
)
from src.qr_generator import gerar_qr
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
semear_usuarios_iniciais()


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

        /* Impressão — página de verificação: some com o cromo do Streamlit e
           com os controles que não fazem sentido num comprovante impresso. */
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


def _selo_status(icone: str, titulo: str, cor: str, cor_fundo: str, subtitulo: str = "") -> None:
    """Selo grande e inequívoco de status, no padrão de validadores oficiais (gov.br/ITI, Atesta CFM).

    `subtitulo` é sempre escapado aqui — hardening defensivo, mesmo que os
    chamadores atuais já escapem valores dinâmicos (ex.: revogado_em) antes
    de passá-los, para evitar regressões se um novo call site esquecer disso.
    """
    subtitulo_html = (
        f'<p style="color:{COR_TEXTO}; font-size:0.95rem; max-width:32rem; '
        f'margin:0.5rem auto 0 auto;">{html.escape(subtitulo)}</p>'
        if subtitulo
        else ""
    )
    st.markdown(
        f"""
        <div style="text-align:center; padding:1.6rem 1rem 0.6rem 1rem;">
            <div style="width:76px; height:76px; border-radius:50%; background-color:{cor_fundo};
                        display:flex; align-items:center; justify-content:center; margin:0 auto 1rem auto;
                        font-size:2.3rem; line-height:1;">
                {icone}
            </div>
            <h1 style="color:{cor}; margin:0; font-size:1.55rem; font-weight:800;">{titulo}</h1>
            {subtitulo_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _frase_confianca() -> None:
    st.markdown(
        f"""
        <p style="text-align:center; color:{COR_TEXTO}; opacity:0.85; font-size:0.9rem;
                  margin:0 0 1.4rem 0;">
            🛡️ Atestado emitido e registrado na plataforma AmorSaúde
        </p>
        """,
        unsafe_allow_html=True,
    )


def _bloco_metadados_verificacao(codigo: str, rotulo_codigo: str = "Código de autenticação") -> None:
    """Bloco discreto de metadados da consulta, no padrão de recibo de verificação oficial."""
    agora = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")
    st.markdown(
        f"""
        <div style="background-color:{COR_FUNDO_CLARO}; border:1px solid {COR_BORDA};
                    border-radius:10px; padding:0.85rem 1.1rem; margin-top:1rem; font-size:0.82rem;
                    color:{COR_TEXTO};">
            <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:0.4rem 1rem;">
                <span><strong>Verificado em:</strong> {agora}</span>
                <span style="word-break:break-all;"><strong>{rotulo_codigo}:</strong> <code>{html.escape(codigo)}</code></span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _campo_dado(rotulo: str, valor: str) -> None:
    """Par rótulo/valor sem truncar texto longo (ao contrário de st.metric)."""
    st.markdown(
        f"""
        <div style="margin-bottom:1rem;">
            <div style="color:{COR_TEXTO}; opacity:0.7; font-size:0.82rem; margin-bottom:0.15rem;">{rotulo}</div>
            <div style="color:{COR_TEXTO}; font-size:1.35rem; font-weight:700; line-height:1.25;
                        word-break:break-word;">{html.escape(str(valor))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _bloco_como_funciona() -> None:
    st.markdown(
        f"""
        <div style="background-color:{COR_BRANCO}; border:1px solid {COR_BORDA}; border-radius:12px;
                    padding:1rem 1.2rem; margin-top:1rem; font-size:0.85rem; color:{COR_TEXTO};">
            <strong>🔎 Como funciona esta verificação</strong><br/><br/>
            A autenticidade deste atestado é confirmada diretamente na fonte — a base de dados da
            plataforma AmorSaúde — a cada consulta feita por este link ou QR Code. Nenhum dado de quem
            realiza esta consulta é coletado ou armazenado.
        </div>
        """,
        unsafe_allow_html=True,
    )


def _botao_imprimir() -> None:
    """Botão que abre a caixa de impressão do navegador para gerar um comprovante limpo."""
    html_conteudo = f"""
    <button id="btn-imprimir-comprovante"
            style="background-color:{COR_BRANCO}; color:{COR_PRIMARIA}; border:1px solid {COR_PRIMARIA};
                   border-radius:8px; padding:0.55rem 1rem; cursor:pointer; font-size:0.88rem;
                   font-weight:600; width:100%; font-family:sans-serif;">
        🖨️ Imprimir comprovante
    </button>
    <script>
        document.getElementById("btn-imprimir-comprovante").addEventListener("click", function() {{
            window.parent.print();
        }});
    </script>
    """
    components.html(html_conteudo, height=48)


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

    with st.expander(f"🔑 Token de API — {usuario_alvo['nome']}", expanded=bool(token_recem_gerado)):
        st.markdown(
            "O token de API identifica este médico perante o endpoint de registro "
            "automático de atestados (ver seção **API / Integrações**). Trate-o como "
            "uma senha: qualquer chamada feita com ele é registrada em nome deste médico."
        )

        if token_recem_gerado:
            st.warning(
                "⚠️ Copie o token agora — por segurança, ele não será exibido novamente. "
                "Ao gerar um novo token, este deixa de funcionar.",
                icon="⚠️",
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
                rotulo = "🔄 Gerar novo token" if tem_token else "➕ Gerar token de API"
                if st.button(rotulo, key=chave_gerar, use_container_width=True, type="primary"):
                    novo_token = gerar_token()
                    salvar_token_api(usuario_alvo["id"], hash_token(novo_token), novo_token[-4:])
                    st.session_state[chave_token_novo] = novo_token
                    st.rerun()
            with col_revogar:
                if tem_token:
                    if st.button("🚫 Revogar token", key=f"revogar_btn_{chave_confirmar_revogar}", use_container_width=True, type="secondary"):
                        st.session_state[chave_confirmar_revogar] = True
                        st.rerun()

        if st.session_state.get(chave_confirmar_revogar):
            st.warning("Tem certeza que deseja revogar este token? Chamadas de API feitas com ele passam a ser recusadas imediatamente.", icon="⚠️")
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
    with st.expander("🔌 API / Integrações"):
        endereco_registro = f"{_url_base()}api/atestados"
        endereco_qr = f"{_url_base()}api/atestados/{{codigo}}/qrcode.png"
        st.markdown(
            f"""
Além do formulário acima, é possível registrar atestados **automaticamente**, de um
sistema externo (por exemplo, uma automação que preenche uma "ficha padrão" e
gera um documento no Canva). Isso é feito chamando um endereço da API com o
**token de API do médico** (gere um na seção "🔑 Token de API" acima).

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

### 🤖 Conectar diretamente na Claude (conversa faz o registro por você)

Além da API acima (para sistemas/automações), também é possível deixar a
própria Claude registrar atestados durante uma conversa, sem nenhum código —
usando um **conector MCP**. Depois de conectado, basta pedir à Claude algo como
*"registre um atestado para [paciente], CID [código], hoje, 3 dias de
afastamento"* e ela chama a ferramenta e devolve o código, o link de
verificação e o QR Code.

**Endereço do conector** (o mesmo endereço serve para todos os médicos —
não leva token nenhum, o login é feito depois, na própria Claude):

```
{endereco_registro.rsplit("/api/atestados", 1)[0]}/mcp
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
(e não mais o token de API), revogar o "🔑 Token de API" acima **não** afeta
o conector MCP. Para desconectar todos os acessos já concedidos à Claude (por
exemplo, se você suspeita que alguém mais teve acesso à sua conta), use o
botão abaixo.
            """
        )
        st.divider()
        usuario_atual = st.session_state.get("usuario")
        if usuario_atual:
            qtd_ativos = contar_oauth_access_tokens_ativos(usuario_atual["id"])
            if qtd_ativos:
                st.caption(f"Conector Claude: {qtd_ativos} acesso(s) autorizado(s) e ainda válido(s).")
                if st.button("🚫 Revogar acesso do conector Claude", key="revogar_oauth_mcp"):
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
                    icone="⚠️",
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

                if status == "revogado":
                    # _selo_status escapa `subtitulo` internamente — não escapar aqui
                    # de novo, senão o texto apareceria com entidades HTML duplicadas.
                    _selo_status(
                        icone="🚫",
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
                        icone="✅",
                        titulo="Atestado Autêntico",
                        cor=COR_PRIMARIA,
                        cor_fundo=COR_FUNDO_CLARO,
                    )

                _frase_confianca()

                st.markdown(
                    f'<p style="color:{COR_TEXTO}; font-weight:700; margin-bottom:0.2rem;">'
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
                        _campo_dado("Diagnóstico (CID)", "🔒 Protegido por sigilo médico")
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

            with st.expander("🔑 Credenciais iniciais — protótipo (clique para ver)", expanded=True):
                st.markdown(
                    f"**Administrador inicial** — usuário: `{ADMIN_INICIAL['usuario']}` / "
                    f"senha: `{ADMIN_INICIAL['senha']}` — use para criar e gerenciar contas de médico."
                )
                st.markdown("**Contas de médico para teste:**")
                colunas = st.columns(len(MEDICOS_TESTE))
                for col, m in zip(colunas, MEDICOS_TESTE):
                    with col:
                        st.markdown(
                            f"**{m['nome']}**  \n"
                            f"Usuário: `{m['usuario']}`  \n"
                            f"Senha: `{m['senha']}`  \n"
                            f"CRM: {m['crm']}"
                        )
                st.caption(
                    "As senhas acima só existem em texto puro nesta nota — no banco de dados "
                    "elas ficam sempre protegidas por hash."
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
                    conta = autenticar(usuario.strip(), senha)
                    if conta:
                        st.session_state["usuario"] = conta
                        st.rerun()
                    else:
                        st.error(
                            "Usuário ou senha inválidos, ou conta desativada. "
                            "Verifique as credenciais iniciais acima."
                        )

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

    conteudo_direita = (
        f'<div style="font-size:1.2rem; font-weight:700;">{html.escape(admin["nome"])}</div>'
        f'<div style="font-size:0.88rem; opacity:0.92;">Administrador</div>'
    )
    _barra_cabecalho(conteudo_direita)

    col_espaco, col_sair = st.columns([5, 1])
    with col_sair:
        if st.button("Sair", use_container_width=True, type="secondary", key="sair_admin"):
            del st.session_state["usuario"]
            st.rerun()

    st.markdown(f'<h3 style="color:{COR_PRIMARIA}; margin-top:0;">👩‍⚕️ Cadastrar médico</h3>', unsafe_allow_html=True)

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
        criar = st.form_submit_button("➕ Criar conta de médico", use_container_width=True, type="primary")

    if criar:
        erros = []
        if not nome_medico.strip():
            erros.append("Informe o nome completo do médico.")
        if not crm_medico.strip():
            erros.append("Informe o CRM (com UF).")
        if not usuario_medico.strip():
            erros.append("Informe um usuário de acesso.")
        if not senha_inicial or len(senha_inicial) < 6:
            erros.append("A senha inicial deve ter pelo menos 6 caracteres.")

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
                st.success(f"✅ Conta criada para {nome_medico.strip()}.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("Esse nome de usuário já está em uso. Escolha outro.")

    st.write("")
    st.divider()

    st.markdown(f'<h3 style="color:{COR_PRIMARIA};">📋 Médicos cadastrados</h3>', unsafe_allow_html=True)

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
                        f'<span style="font-size:1.05rem; font-weight:700; color:{COR_TEXTO};">'
                        f'{html.escape(m["nome"])}</span><br>'
                        f'<span style="color:{COR_TEXTO}; opacity:0.75; font-size:0.85rem;">'
                        f'{html.escape(m["crm"] or "")} · usuário: {html.escape(m["usuario"])}</span>',
                        unsafe_allow_html=True,
                    )
                with col_status:
                    if m["ativo"]:
                        st.markdown(
                            f'<span style="background:{COR_FUNDO_CLARO}; color:{COR_PRIMARIA}; '
                            f'padding:0.2rem 0.6rem; border-radius:20px; font-size:0.78rem; font-weight:700;">● Ativo</span>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f'<span style="background:#FBEAEA; color:{COR_SECUNDARIA}; '
                            f'padding:0.2rem 0.6rem; border-radius:20px; font-size:0.78rem; font-weight:700;">● Inativo</span>',
                            unsafe_allow_html=True,
                        )
                with col_acoes:
                    col_toggle, col_reset = st.columns(2)
                    with col_toggle:
                        rotulo = "Desativar" if m["ativo"] else "Ativar"
                        if st.button(rotulo, key=f"toggle_{m['id']}", use_container_width=True, type="secondary"):
                            definir_status_usuario(m["id"], not m["ativo"])
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
                        if not nova_senha or len(nova_senha) < 6:
                            st.error("A nova senha deve ter pelo menos 6 caracteres.")
                        else:
                            redefinir_senha_usuario(m["id"], gerar_hash_senha(nova_senha))
                            st.session_state.pop(chave_reset, None)
                            st.success("✅ Senha redefinida com sucesso.")
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
        f'<div style="font-size:1.2rem; font-weight:700;">{medico["nome"]}</div>'
        f'<div style="font-size:0.88rem; opacity:0.92;">{medico["especialidade"]} · {medico["crm"]}</div>'
    )
    _barra_cabecalho(conteudo_direita)

    col_espaco, col_sair = st.columns([5, 1])
    with col_sair:
        if st.button("Sair", use_container_width=True, type="secondary"):
            del st.session_state["usuario"]
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

    st.write("")
    st.divider()
    st.markdown(f'<h3 style="color:{COR_PRIMARIA};">🔌 Registro automático (API)</h3>', unsafe_allow_html=True)
    _secao_token_api(conta_atual, quem_gerencia="medico")
    _secao_api_integracoes()

    _rodape()


# ---------------------------------------------------------------------------
# Roteador principal
# ---------------------------------------------------------------------------

codigo_url = st.query_params.get("codigo")

if codigo_url:
    tela_verificacao(str(codigo_url))
elif "usuario" not in st.session_state:
    tela_login()
elif st.session_state["usuario"]["perfil"] == "admin":
    tela_admin()
else:
    tela_dashboard()
