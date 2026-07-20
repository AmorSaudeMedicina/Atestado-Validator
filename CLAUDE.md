# CLAUDE.md — Contexto do Projeto: Validador/Emissor de Atestados (AmorSaúde)

> Este arquivo é lido automaticamente pelo Claude Code. Ele resume a ideia, a
> arquitetura, as decisões já tomadas e os próximos passos, para que qualquer
> sessão continue o projeto com contexto completo. Leia também o código real do
> projeto para confirmar nomes de arquivos e detalhes de implementação — este
> documento descreve a intenção e o histórico; o código é a fonte da verdade atual.

## 1. O que é o projeto

Uma plataforma da **AmorSaúde** (rede de clínicas) para **emitir atestados médicos
com QR Code** e permitir que **empresas e pacientes verifiquem a autenticidade**
pelo QR. A plataforma é a **fonte de verdade** (modelo parecido com o que a Memed
faz para receitas): o atestado nasce registrado no sistema, ganha um QR, e quem
tiver o QR confirma os dados oficiais.

É uma **ferramenta de apoio** à decisão humana (RH/auditoria). **Nunca** emite
veredito de "fraude confirmada".

### Atores
- **Médico:** faz login, emite atestados (por formulário OU pelo fluxo automatizado
  via Claude+Canva), e pode revogar atestados que emitiu.
- **Administrador:** gerencia contas de médico (criar, ativar/desativar, redefinir senha).
- **Empresa/Paciente:** verificam a autenticidade pela página pública (via QR), sem login.

## 2. Stack técnica
- **Python + Streamlit** (interface principal: login, dashboard, página de verificação).
- **API/servidor complementar** (dentro do mesmo app) para: API REST, servidor **MCP**
  (conector para a Claude) e o endpoint público da imagem do QR.
- **Banco de dados SQLite** (persistente).
- Geração de QR Code (biblioteca `qrcode`).
- **Hospedagem atual:** **GitHub + Railway** (deploy a partir do repositório no GitHub),
  instância única sempre ligada, em produção em
  `https://atestado-validator-production.up.railway.app`.

> IMPORTANTE (hospedagem): o app **precisa rodar como instância única sempre-ligada**
> por causa do SQLite. Em cenário multi-instância o banco fica inconsistente
> (cada instância teria seu próprio SQLite). Se um dia crescer além de uma
> instância, migre o banco para um Postgres gerenciado.

### Variáveis de ambiente (Railway, produção)
- **`DATA_DIR`** — diretório persistente (ex.: um Volume do Railway) onde fica o
  arquivo `atestados.db`, para o banco sobreviver a redeploys.
- **`ADMIN_INITIAL_PASSWORD`** — senha da conta `admin` inicial na primeira subida;
  se ausente, o app gera uma senha aleatória forte e a escreve uma única vez no
  log de inicialização (nunca fica hardcoded no código).
- **`SEED_TEST_DATA`** — só deve ser `"true"` em ambiente de teste/local; quando
  definida, cria médicos de teste com senhas fracas conhecidas. **Nunca definir em produção.**
- **`ENCRYPTION_KEY`** — chave simétrica (Fernet) usada para criptografar em repouso
  os dados sensíveis dos atestados (nome do paciente, CID); obrigatória — o processo
  falha ao subir (fail-closed) se estiver ausente ou inválida.
- **`AUDIT_RETENTION_DAYS`** (opcional) — quantos dias manter os eventos da trilha
  de auditoria antes de serem apagados automaticamente; padrão 365 dias se ausente/inválida.
- **`ATESTADO_RETENTION_DAYS`** (opcional, **DESLIGADA por padrão**) — retenção
  automática dos ATESTADOS (não confundir com `AUDIT_RETENTION_DAYS`, que é só da
  trilha de auditoria). Se ausente/vazia/0/inválida, nada é apagado nem anonimizado
  automaticamente — o prazo de guarda de registro médico é decisão jurídica, então a
  automação só liga com essa variável definida explicitamente. Se definida com um
  número de dias > 0, ANONIMIZA (nunca exclui) os atestados emitidos há mais tempo
  que esse prazo, na subida do processo e a cada 24h.

## 3. Funcionalidades já implementadas
- **Login seguro:** perfis **admin** e **médico**, senhas com **hash (bcrypt)**,
  sessões, telas protegidas, "fail-closed".
- **Painel do admin:** criar/listar médicos, **ativar/desativar**, **redefinir senha**.
  Admin inicial criado a partir de `ADMIN_INITIAL_PASSWORD` (ou senha aleatória forte
  gerada no primeiro boot, ver seção de variáveis de ambiente).
- **Segurança/LGPD — Parte 1 (acesso/login), concluída:** nenhuma credencial aparece
  na tela, exigência de senha forte, bloqueio de conta por tentativas de login
  incorretas, expiração de sessão, e troca de senha obrigatória no primeiro login do admin.
- **Segurança/LGPD — Parte 2 (criptografia), concluída:** dados sensíveis dos atestados
  (nome do paciente, CID) são criptografados em repouso no banco (Fernet, chave em
  `ENCRYPTION_KEY`).
- **Segurança/LGPD — Parte 3 (auditoria), concluída:** trilha de auditoria registra
  eventos relevantes (login, emissão, revogação, ações de admin), com tela própria
  no painel do admin para consulta e retenção configurável (`AUDIT_RETENTION_DAYS`).
- **Segurança/LGPD — Parte 4 (retenção/exclusão de atestados), concluída:**
  - **Anonimizar:** remove nome do paciente e CID de um atestado, mantendo código,
    datas, período e status (`anonimizado`). A página pública de um atestado
    anonimizado indica que o registro existiu mas os dados pessoais foram
    removidos, sem quebrar.
  - **Ferramenta manual (só admin)**, tela "Retenção/Exclusão" no painel: localizar
    um atestado pelo código e ANONIMIZAR ou EXCLUIR definitivamente (com
    confirmação explícita — excluir exige digitar o código de novo). Pensada para
    atender pedidos de titular (direito de exclusão da LGPD). Ambas as ações vão
    para a trilha de auditoria, só com o código do atestado.
  - **Retenção automática, opt-in, DESLIGADA por padrão** (`ATESTADO_RETENTION_DAYS`
    — ver seção de variáveis de ambiente): só anonimiza (nunca exclui), e só se a
    variável for definida explicitamente.
  - Implementação: `src/retencao.py` (regras de negócio, nunca derruba a
    aplicação) + funções novas em `src/database.py` + eventos novos em
    `src/audit.py`.
- **Emissão por formulário:** paciente, CID, data de emissão, período/dias. Médico vem da sessão.
- **Geração de QR:** código aleatório único; URL de verificação; imagem PNG pública em
  `/atestados/{codigo}/qrcode.png` (com CORS, sem login, cacheável).
- **Página pública de verificação** (`/?codigo=...`): mostra estado **Autêntico /
  Revogado / Não encontrado**, com dados (médico, CRM, paciente, data, período).
  **O CID (diagnóstico) NÃO aparece na página pública** — é protegido por sigilo médico.
  Inclui metadados de verificação e sinais de confiança.
- **Revogação:** o médico revoga; a verificação passa a mostrar "revogado/inválido".
- **API REST:** registra atestado programaticamente, autenticada por **token por médico**;
  retorna código + URL de verificação + link da imagem do QR.
- **Conector MCP (para a Claude):** autenticação **OAuth 2.0** (Dynamic Client
  Registration + Authorization Code + PKCE). URL do conector:
  `https://atestado-validator-production.up.railway.app/mcp`. Expõe a ferramenta
  **`registrar_atestado`**. O médico faz login com as credenciais do Portal ao conectar.

## 4. Design / identidade visual (AmorSaúde)
- **Paleta:** verde-água/teal `#5FC2D4` (principal), coral `#D74846` (secundária),
  vermelho `#D53A31` (CTA/alerta), texto `#525050`, fundo `#EAF7F9`, branco `#FFFFFF`.
  Regra: coral/vermelho **só** para ações principais e alertas; verde-água como base.
- **Logo:** no cabeçalho de todas as telas (arquivo em `assets/logo-amorsaude.png`).
- **Tipografia:** **Nunito Sans** (escolhida por ser arredondada/quente como a marca,
  profissional e legível), com hierarquia clara de título/rótulo/corpo.
- **Ícones:** conjunto de **ícones de linha** (SVG, estilo Lucide). **Sem emojis** na interface.
- **Espaçamento:** ritmo de **8pt**; variação intencional (evitar visual "chapado").
- **Microinterações:** hover e transições suaves.
- **Mobile:** responsivo; a página de verificação é prioridade no celular (é aberta via QR).

## 5. Fluxo automatizado com Canva (integração principal)
Numa conversa da Claude com os conectores **"AmorSaude Validação" (MCP)** + **Canva**:
1. O usuário envia uma **ficha**: Nome, CPF, Data de início do afastamento, Quantidade de dias, CID.
2. A Claude registra via **`registrar_atestado`** → recebe código + URL de verificação + link do QR.
   - **O CPF NÃO vai para o registro** (fica só no documento) — decisão de LGPD.
   - Período = início (data de início) + dias (quantidade de dias).
3. A Claude edita o template do Canva **"TEMPLATE PARA CLAUDE"** (id `DAHO7Z4z7P8`):
   - Substitui os textos do paciente (find_and_replace no parágrafo).
   - Coloca o QR **no próprio elemento do QR** (que é editável) via `update_fill` — NÃO sobrepor.
   - Garante que o CID no texto bata com o registro.
4. Devolve o link do Canva pronto + código + URL de verificação.

> PENDÊNCIA CONHECIDA: hoje o fluxo edita o template ORIGINAL (sobrescreve a cada ficha).
> O correto é **DUPLICAR o template por ficha** e trabalhar na cópia, preservando o original.

## 6. Decisões e restrições importantes
- Ferramenta de **apoio**, nunca "fraude confirmada".
- **LGPD:** CID protegido na página pública; CPF não vai para a verificação. Frente de
  **Segurança/LGPD CONCLUÍDA** (Partes 1-4): Parte 1 (acesso/login), Parte 2
  (criptografia em repouso), Parte 3 (auditoria) e Parte 4 (retenção/exclusão de
  atestados) — ver seção 3. Não há parte pendente nesta frente.
- Código do QR deve ser **aleatório e imprevisível** (evitar enumeração/vazamento).
- URLs geradas (OAuth redirect, base do QR/verificação) são **dinâmicas** (baseadas no
  domínio da requisição), para funcionar em localhost e em produção sem hardcode.

## 7. Como rodar localmente (a confirmar no código)
1. Instalar **Python 3.11+** e as dependências: `pip install -r requirements.txt`.
2. (Se o OCR estiver em uso — é secundário/opcional) instalar libs de sistema `tesseract` e `zbar`.
3. Rodar o Streamlit: `streamlit run app.py` (config em `.streamlit/config.toml`, porta 5000).
4. O servidor da API/MCP pode subir junto — verificar o comando/estrutura de execução no projeto.
5. O SQLite é criado/usado localmente. As URLs se adaptam ao localhost automaticamente.

## 8. Próximos passos / backlog
- **Fluxo Canva:** duplicar o template por ficha (não sobrescrever o original).
- **Visual da página pública de verificação:** cabeçalho, logo clicável, e código de
  autenticação com botão de copiar — ainda pendente.
- **Design:** continuar lapidando (a rodada feita cobriu ícones, tipografia, espaçamento,
  microinterações, cor e mobile).
- **Atestado final em PDF** com o QR embutido (era um recurso planejado para "mais pra frente").

## 9. Como trabalhar neste projeto (preferências)
- Explicar em linguagem simples (o "porquê", não só o "como") — o dono não é dev experiente.
- Preferir soluções simples e incrementais.
- Sempre sinalizar implicações de privacidade/LGPD (dados sensíveis de saúde).
- Antes de mudanças maiores, resumir o que será feito e confirmar.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
