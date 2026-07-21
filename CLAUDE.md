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
- **Médico:** faz login, emite atestados pelo formulário (com PDF gerado
  automaticamente via Canva se informar o CPF do paciente — ver seção 5) OU pelo
  fluxo manual em conversa com a Claude+Canva, e pode revogar atestados que emitiu.
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
- **`CANVA_CLIENT_ID`** / **`CANVA_CLIENT_SECRET`** — credenciais da Integration
  criada em canva.com/developers, usadas pelo servidor para se autenticar no Canva
  (OAuth 2.0 + PKCE) e gerar o PDF do atestado automaticamente. Sem essas duas
  variáveis, a geração automática do PDF fica indisponível (o resto do app funciona
  normalmente) — ver seção 5 para o passo a passo completo, incluindo o aviso de
  **reautorizar ao trocar de conta do Canva**.
- **`CANVA_TEMPLATE_DESIGN_ID`** (opcional) — id do design "TEMPLATE PARA CLAUDE" no
  Canva; padrão `DAHO7Z4z7P8` (o mesmo já usado pelo fluxo de chat) se ausente.
- **`CANVA_CAMPO_NOME`**, **`CANVA_CAMPO_CPF`**, **`CANVA_CAMPO_DATA_INICIO`**,
  **`CANVA_CAMPO_DIAS`**, **`CANVA_CAMPO_CID`**, **`CANVA_CAMPO_QR`** (todas
  opcionais) — nomes dos campos de autofill marcados no template no editor do
  Canva, caso precisem ser diferentes dos padrões (`nome`, `cpf`, `data_inicio`,
  `dias`, `cid`, `qr_code`). Ver seção 5.

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
- **Documento PDF automático via Canva** (sem IA no meio — ver seção 5 para o fluxo
  completo): ao emitir um atestado (formulário, API ou MCP) com o CPF do paciente
  informado, o servidor gera sozinho o PDF do atestado (template preenchido +
  QR embutido) em segundo plano, disponível para baixar no dashboard do médico
  assim que terminar. Implementação: `src/canva_client.py` (pipeline Canva Connect
  API), `src/canva_admin.py` (autorização OAuth do servidor, uma vez, pelo admin),
  tabelas novas `documentos_atestado`/`canva_oauth_token`/`canva_oauth_state`.

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

## 5. Geração do PDF via Canva — fluxo AUTOMÁTICO (principal) + fluxo manual (fallback)

Existem HOJE dois jeitos de gerar o PDF do atestado (template do Canva preenchido +
QR embutido). O automático é o principal; o manual continua funcionando como
alternativa, caso o automático falhe (token expirado, Canva fora do ar, etc.) —
ver ponto 5 do pedido original desta funcionalidade.

### 5.1 Fluxo AUTOMÁTICO — direto do servidor, sem Claude no meio

Disparado sozinho sempre que um atestado é emitido (formulário, API ou MCP) **com
o CPF do paciente informado** (campo opcional — sem CPF, nenhum PDF é gerado, mas
o atestado e o QR são emitidos normalmente). Roda em segundo plano (thread), nunca
trava a emissão; se falhar, o atestado continua válido e o dashboard oferece
"Tentar gerar PDF novamente".

**Por que não é "duplicar + editar" como o fluxo de chat fazia:** a API pública do
Canva (Connect API) não tem um endpoint genérico de duplicar design nem de editar
o conteúdo de um elemento específico — só a **Autofill API**
(`POST /v1/autofills`, `create_from_design`), que já cria um design **novo**
preenchendo campos previamente marcados (nunca toca no original). Por isso o
pré-requisito 2 abaixo é obrigatório.

Pipeline (`src/canva_client.py`): sobe o QR como asset → roda o autofill do
template (`design_id` = `CANVA_TEMPLATE_DESIGN_ID`, padrão `DAHO7Z4z7P8`) → exporta
o design resultante em PDF → baixa e grava em `DATA_DIR/documentos/{codigo}.pdf.enc`,
**cifrado com a mesma `ENCRYPTION_KEY`** (o PDF carrega nome e CPF em claro dentro
do documento, então merece o mesmo cuidado já dado a nome/CID no banco). Ao
anonimizar ou excluir um atestado (Parte 4 de Segurança/LGPD), esse PDF também é
apagado — senão a anonimização no banco não adiantaria nada para os dados que já
estivessem gravados dentro do PDF.

**Pré-requisitos — feitos manualmente, fora do alcance do código:**

1. **Uma Integration no Canva** (canva.com/developers → "Your integrations" →
   "Create an integration", tipo **Public** — não precisa de aprovação do Canva
   para você mesmo autorizar sua própria conta, só para publicar para outros
   usuários). Escopos necessários: `design:content` (Read+Write), `design:meta`
   (Read), `asset` (Read+Write). Redirect URI:
   `{domínio do app}/admin/canva/callback` (ex.:
   `https://atestado-validator-production.up.railway.app/admin/canva/callback`).
   Client ID/Secret vão em `CANVA_CLIENT_ID`/`CANVA_CLIENT_SECRET`.
2. **Campos de autofill marcados no template** "TEMPLATE PARA CLAUDE" (mesmo
   design da id `DAHO7Z4z7P8`), no editor do Canva — cada elemento dinâmico
   (nome, CPF, data de início, dias, CID como texto; o elemento do QR como
   imagem) precisa estar marcado como campo de dados/autofill, com um nome
   configurável via `CANVA_CAMPO_*` (ver seção 2; padrões: `nome`, `cpf`,
   `data_inicio`, `dias`, `cid`, `qr_code`).
3. **Um administrador autoriza o servidor uma única vez** em
   `/admin/canva/conectar` (link também disponível no painel do admin) — tela de
   login própria (usuário/senha de admin), depois redireciona para o Canva
   autorizar. O token fica guardado **cifrado no banco** (nunca em texto puro,
   nunca no código/GitHub), com renovação automática via refresh token (o Canva
   usa refresh token de uso único — cada renovação grava um novo).

> ⚠️ **CONTA DE TESTE DO CANVA — REAUTORIZAR AO TROCAR PARA PRODUÇÃO:** a
> Integration/conta usada hoje é uma conta de **TESTE**. Quando trocar para a
> conta de produção do Canva, é preciso **refazer o passo 3** — acessar
> `/admin/canva/conectar` de novo, já logado (no navegador) na conta de Canva de
> produção. A nova autorização substitui automaticamente o token anterior (é uma
> tabela de uma linha só, sempre sobrescrita). Se o template também mudar de
> conta, atualize `CANVA_TEMPLATE_DESIGN_ID` e confira se os nomes dos campos de
> autofill batem com `CANVA_CAMPO_*`.

### 5.2 Fluxo MANUAL — conversa com a Claude (fallback)

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

> PENDÊNCIA CONHECIDA (só deste fluxo manual — o automático acima nunca toca no
> original, por construção): o fluxo de chat edita o template ORIGINAL
> (sobrescreve a cada ficha). O correto seria **DUPLICAR o template por ficha**
> e trabalhar na cópia. Baixa prioridade agora que o fluxo automático é o principal.

## 6. Decisões e restrições importantes
- Ferramenta de **apoio**, nunca "fraude confirmada".
- **LGPD:** CID protegido na página pública; CPF não vai para a verificação nem para
  o registro do atestado em NENHUM fluxo (formulário, API, MCP) — só existe,
  quando informado, para preencher o PDF gerado via Canva (seção 5), nunca é
  persistido em lugar nenhum (nem para permitir "tentar novamente" — o dashboard
  pede o CPF de novo nesse caso). Frente de **Segurança/LGPD CONCLUÍDA** (Partes
  1-4): Parte 1 (acesso/login), Parte 2 (criptografia em repouso), Parte 3
  (auditoria) e Parte 4 (retenção/exclusão de atestados) — ver seção 3. Não há
  parte pendente nesta frente.
- O PDF gerado via Canva é cifrado em repouso (mesma `ENCRYPTION_KEY`) e é apagado
  junto quando o atestado é anonimizado/excluído (Parte 4) — ver seção 5.1.
- O token OAuth do Canva nunca fica em variável de ambiente nem em texto puro:
  fica cifrado no banco (`ENCRYPTION_KEY`), renovado automaticamente.
- Código do QR deve ser **aleatório e imprevisível** (evitar enumeração/vazamento).
- URLs geradas (OAuth redirect, base do QR/verificação) são **dinâmicas** (baseadas no
  domínio da requisição), para funcionar em localhost e em produção sem hardcode —
  EXCETO o redirect URI do Canva (seção 5.1), que precisa ser um valor fixo
  cadastrado na Integration (o Canva não aceita redirect dinâmico).

## 7. Como rodar localmente (a confirmar no código)
1. Instalar **Python 3.11+** e as dependências: `pip install -r requirements.txt`.
2. (Se o OCR estiver em uso — é secundário/opcional) instalar libs de sistema `tesseract` e `zbar`.
3. Rodar o Streamlit: `streamlit run app.py` (config em `.streamlit/config.toml`, porta 5000).
4. O servidor da API/MCP pode subir junto — verificar o comando/estrutura de execução no projeto.
5. O SQLite é criado/usado localmente. As URLs se adaptam ao localhost automaticamente.
6. Geração de PDF via Canva é **opcional** localmente: sem `CANVA_CLIENT_ID`/
   `CANVA_CLIENT_SECRET` definidos, o resto do app funciona normalmente — só a
   geração do PDF fica indisponível (mensagem clara no dashboard/admin, nunca erro).

## 8. Próximos passos / backlog
- **Fluxo Canva manual (chat):** duplicar o template por ficha em vez de editar o
  original — baixa prioridade agora que o fluxo automático (seção 5.1, que já
  nunca edita o original) é o principal.
- **Design:** continuar lapidando (as rodadas feitas cobriram ícones, tipografia,
  espaçamento, microinterações, cor, mobile, cabeçalho da verificação e tema
  claro/escuro da página pública).
- **PDF via Canva:** hoje o status só aparece no dashboard do médico — considerar
  expor também na resposta da API/MCP (ex.: um campo `documento_status`) se fizer
  sentido para quem integra via API/Canva/Make/Zapier.

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
