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
  automaticamente, localmente no servidor, se informar o CPF do paciente — ver
  seção 5) OU pelo fluxo manual em conversa com a Claude+Canva, e pode revogar
  atestados que emitiu.
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
- **`CANVA_CLIENT_ID`** / **`CANVA_CLIENT_SECRET`** / **`CANVA_TEMPLATE_DESIGN_ID`** /
  **`CANVA_CAMPO_NOME`**/**`CANVA_CAMPO_CPF`**/**`CANVA_CAMPO_DATA_INICIO`**/
  **`CANVA_CAMPO_DIAS`**/**`CANVA_CAMPO_CID`**/**`CANVA_CAMPO_QR`** — **legado,
  não usadas pela geração automática do PDF desde que ela passou a rodar
  localmente com weasyprint** (ver seção 5). Continuam existindo só porque
  `src/canva_client.py`/`src/canva_admin.py` (e a tela `/admin/canva/conectar`)
  não foram removidos do código — o fluxo manual via chat (seção 5.2) não
  depende delas, ele usa o conector Canva da própria Claude. Podem ficar
  ausentes sem problema nenhum.

## 3. Funcionalidades já implementadas
- **Login seguro:** perfis **admin** e **médico**, senhas com **hash (bcrypt)**,
  sessões, telas protegidas, "fail-closed".
- **Painel do admin:** criar/listar médicos, **ativar/desativar**, **redefinir senha**,
  e gerar/revogar o **token de API** de cada médico (o dashboard do próprio médico
  não tem mais essa gestão — ver "Dashboard do médico" abaixo). Admin inicial
  criado a partir de `ADMIN_INITIAL_PASSWORD` (ou senha aleatória forte gerada
  no primeiro boot, ver seção de variáveis de ambiente). O cadastro de médico
  tem também um bloco **opcional** de endereço da clínica (rua/número, cidade, UF,
  CEP, telefone) — usado só para preencher o rodapé do PDF do atestado (seção 5.1);
  sem preencher, o rodapé simplesmente omite as linhas correspondentes.
- **Dashboard do médico:** cartões de visão geral, gráfico de atestados emitidos
  por mês (sempre os **últimos 6 meses**, mesmo com meses zerados — evita o
  visual de "barra isolada" com poucos dados), formulário de emissão e lista de
  atestados emitidos — cada item da lista mostra discretamente (ícone + texto
  pequeno) se o CID está "Visível na verificação" ou "Oculto na verificação"
  para aquele atestado (ver checkbox de emissão e seção 6). **Não tem** gestão
  de token de API/integrações (isso ficou só no painel do admin, ver acima) —
  um médico que precise do token pede ao administrador.
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
- **Emissão por formulário:** paciente, CID, data de emissão, período/dias, e o
  checkbox **"Exibir diagnóstico (CID) na verificação pública"** (desmarcado
  por padrão — ver seção 6). Médico vem da sessão. O mesmo campo
  (`exibir_cid`, opcional, padrão `false`) é aceito pela API REST e pelo
  conector MCP — quem integra por esses caminhos também decide na hora de
  registrar, não é algo que se muda depois.
- **Geração de QR:** código aleatório único; URL de verificação; imagem PNG pública em
  `/atestados/{codigo}/qrcode.png` (com CORS, sem login, cacheável).
- **Página pública de verificação** (`/?codigo=...`): mostra estado **Autêntico /
  Revogado / Não encontrado**, com "Dados validados" nesta ordem: paciente, CPF
  censurado (só se houver — hoje nunca há, CPF nunca é persistido, ver seção 6),
  data de emissão + dias numa linha (ex.: "26/07/2026 · 1 dia(s)"), CID
  (diagnóstico) e médico/CRM. **Se o CID aparece ou não é decisão do médico,
  tomada na emissão** (checkbox acima) — quem consulta a página pública não
  tem mais nenhum controle sobre isso (não existe toggle/botão na página
  pública; essa é uma mudança de decisão em relação à versão anterior, que
  deixava a critério de quem consultava). A censura do CPF é aplicada em
  `_preparar_dados_verificacao_publica()` (o dict que chega na renderização
  já vem com o valor mascarado — nunca o CPF completo escondido só por
  CSS). Cabeçalho no estilo do site oficial (amorsaude.com.br): faixa de
  **largura total da página** (técnica CSS "full bleed" —
  `position:relative; left:50%; margin-left:-50vw; width:100vw`,
  necessária porque `layout="centered"` limita o container padrão do
  Streamlit a uma coluna central), só com a logo centralizada em altura
  fixa de 48px (forçada via CSS, `.amorsaude-cabecalho-publico img`), linha
  fina de separação abaixo — diferente do fundo verde-água claro do
  restante da página. **Reage a `prefers-color-scheme: dark`**: fundo
  branco/`#E0E0E0` no tema claro (via `var(--pub-cartao)`, igual ao resto
  da página), fundo escuro/borda discreta no tema escuro (via
  `var(--pub-borda)`). A logo (PNG com transparência real, sem fundo
  embutido) aparece direto sobre o fundo do cabeçalho em qualquer tema, sem
  nenhum container/chip ao redor. A barra nativa do Streamlit
  (`stHeader`, botão "Deploy") e o padding-top padrão do container ficam
  escondidos/zerados só nesta tela (`.stApp:has(.st-key-pagina-publica)`),
  para o cabeçalho ficar colado no topo de verdade. Título da aba do navegador só
  nesta tela ("AmorSaúde — Validador de Atestados"), via
  `_definir_titulo_aba_publica()`/`components.html()` (`st.set_page_config()`
  é global ao processo, não dá para variar por tela). Inclui metadados de
  verificação e sinais de confiança.
- **Revogação:** o médico revoga; a verificação passa a mostrar "revogado/inválido".
- **API REST:** registra atestado programaticamente, autenticada por **token por
  médico** (gerado/revogado pelo admin no painel — ver seção 3); retorna código
  + URL de verificação + link da imagem do QR.
- **Conector MCP (para a Claude):** autenticação **OAuth 2.0** (Dynamic Client
  Registration + Authorization Code + PKCE). URL do conector:
  `https://atestado-validator-production.up.railway.app/mcp`. Expõe a ferramenta
  **`registrar_atestado`**. O médico faz login com as credenciais do Portal ao conectar.
- **Documento PDF automático, gerado localmente** (sem Canva, sem IA no meio —
  ver seção 5 para o fluxo completo): ao emitir um atestado (formulário, API
  ou MCP) com o CPF do paciente informado, o servidor monta um HTML/CSS fiel
  ao layout oficial do atestado e renderiza o PDF sozinho, em segundo plano,
  com **weasyprint** — sem depender de nenhum serviço externo. Disponível
  para baixar no dashboard do médico assim que terminar. Implementação:
  `src/documento_pdf.py` (monta o HTML, renderiza, cifra e salva), tabela
  `documentos_atestado` (já existia, reaproveitada).
- **Feedback ao vivo da geração do PDF (dashboard):** assim que o atestado é
  emitido (com CPF), o dashboard já mostra o indicador "Gerando o PDF..." para
  aquele atestado, sem precisar recarregar a página; a cada poucos segundos a
  tela confere sozinha se o PDF ficou pronto e troca o indicador pelo botão
  "Baixar PDF do atestado" (ou por uma mensagem de erro + botão "Tentar
  gerar PDF novamente", se a geração falhar) — tudo sem reload manual.
  Implementação: `st.fragment(run_every=...)` do Streamlit, isolado à seção do
  PDF de cada atestado (não recarrega o dashboard inteiro). Não usa nenhuma
  variável de ambiente nova.
- **"Lembrar de mim neste dispositivo" (sessão de 30 dias):** checkbox na tela
  de login; quando marcado, mantém o médico logado por 30 dias mesmo sem
  atividade, via cookie **httpOnly** (não acessível por JavaScript), em vez de
  só depender da sessão do Streamlit. Como o Streamlit não tem API para setar
  cookie httpOnly a partir do script, o login gera um token de troca de uso
  único e válido por 60s, redireciona para uma rota HTTP dedicada
  (`GET /auth/lembrar-me`, em `src/auth_routes.py`, registrada em `server.py` —
  mesmo padrão já usado por `/oauth/authorize` e `/admin/canva/conectar`), que
  troca esse token pelo cookie de verdade (30 dias, `Secure` quando em HTTPS,
  `SameSite=Lax`) e redireciona de volta já autenticado. O valor do cookie
  nunca é guardado em texto puro no banco — só o hash (mesmo padrão de
  `src/api_tokens.py`). O token é **revogado** (não funciona mais) ao clicar
  em "Sair" ou ao trocar a senha (própria ou redefinida pelo admin). Sem o
  checkbox marcado, nada muda: a sessão continua expirando por inatividade
  como antes. Implementação: `src/lembrar_me.py` (regra de negócio),
  `src/auth_routes.py` (rota HTTP), tabelas novas
  `lembrar_me_handoff`/`lembrar_me_tokens` em `src/database.py`. Não usa
  nenhuma variável de ambiente nova.

## 4. Design / identidade visual (AmorSaúde)
- **Paleta:** verde-água/teal `#5FC2D4` (principal), coral `#D74846` (secundária),
  vermelho `#D53A31` (CTA/alerta), texto `#525050`, fundo `#EAF7F9`, branco `#FFFFFF`.
  Regra: coral/vermelho **só** para ações principais e alertas; verde-água como base.
- **Logo:** no cabeçalho de todas as telas (arquivo em `assets/logo-amorsaude.png`,
  PNG com transparência real (RGBA), gerado a partir do vetor original
  `assets/logo-amorsaude1.svg` — esse `.svg` fica no repositório só como fonte,
  não é lido pelo app). **Favicon** da página pública de verificação: recorte
  quadrado só do ícone (sem a palavra "amorsaúde", que ficaria ilegível
  minúscula) em `assets/favicon-amorsaude.png`, injetado via
  `_definir_titulo_aba_publica()`/`components.html()` (mesma técnica do
  título da aba — `st.set_page_config(page_icon=...)` é global ao processo,
  não dá pra variar por tela).
- **Tipografia:** **Nunito Sans** (escolhida por ser arredondada/quente como a marca,
  profissional e legível), com hierarquia clara de título/rótulo/corpo.
- **Ícones:** conjunto de **ícones de linha** (SVG, estilo Lucide). **Sem emojis** na interface.
- **Espaçamento:** ritmo de **8pt**; variação intencional (evitar visual "chapado").
- **Microinterações:** hover e transições suaves.
- **Mobile:** responsivo; a página de verificação é prioridade no celular (é aberta via QR).

## 5. Geração do PDF — fluxo AUTOMÁTICO (local, sem Canva) + fluxo manual (Claude + Canva)

Existem HOJE dois jeitos de gerar o PDF do atestado. O automático (principal)
roda inteiramente no servidor, sem nenhum serviço externo. O manual (fallback)
é uma conversa com a Claude usando o conector do Canva, útil quando o médico
quer editar o documento à mão antes de entregar.

### 5.1 Fluxo AUTOMÁTICO — HTML/CSS + weasyprint, direto no servidor

Disparado sozinho sempre que um atestado é emitido (formulário, API ou MCP) **com
o CPF do paciente informado** (campo opcional — sem CPF, nenhum PDF é gerado, mas
o atestado e o QR são emitidos normalmente). Roda em segundo plano (thread), nunca
trava a emissão; se falhar, o atestado continua válido e o dashboard oferece
"Tentar gerar PDF novamente".

**Como funciona (`src/documento_pdf.py`):** monta um HTML/CSS fiel ao layout
oficial do atestado (cabeçalho com o logo, faixa de título verde-água, corpo
com o texto do atestado, bloco de assinatura do médico + QR Code, rodapé
verde-água com endereço/horário), embutindo o logo e o QR Code como imagens
`data:` (base64) direto no HTML — não depende de nenhum arquivo servido por
URL. Renderiza esse HTML em PDF com **weasyprint** e grava em
`DATA_DIR/documentos/{codigo}.pdf.enc`, **cifrado com a mesma
`ENCRYPTION_KEY`** (o PDF carrega nome e CPF em claro dentro do documento,
então merece o mesmo cuidado já dado a nome/CID no banco). Ao anonimizar ou
excluir um atestado (Parte 4 de Segurança/LGPD), esse PDF também é apagado —
senão a anonimização no banco não adiantaria nada para os dados que já
estivessem gravados dentro do PDF.

Dados variáveis por atestado, passados para `disparar_geracao_documento()`:
nome e CPF do paciente, data de início, dias de afastamento, CID, data de
emissão, nome do médico e CRM (esses dois últimos vêm do registro do próprio
atestado — cada atestado mostra o médico que realmente o emitiu, não um nome
fixo). A cidade/UF impressa no CORPO do documento (linha de local/data, perto
da assinatura) é fixa ("Ribeirão Preto, - São Paulo"). Já o **endereço/CEP/
telefone do RODAPÉ** vêm do cadastro do médico (`endereco_rua`,
`endereco_cidade`, `endereco_estado`, `endereco_cep`, `endereco_telefone` —
colunas opcionais em `usuarios`, preenchidas no formulário "Cadastrar médico"
do admin, ver seção 3): cada linha do rodapé só aparece se o campo
correspondente estiver preenchido (nunca mostra um rótulo vazio); sem nenhum
deles, a coluna de endereço do rodapé fica em branco (só o horário de
funcionamento, que é fixo, continua aparecendo). O rodapé usa
`flex: 1 0 auto` no bloco de conteúdo acima dele (não `position: fixed`) para
ficar sempre colado ao final da página A4, inclusive em documentos curtos.

**Dependência de sistema:** weasyprint precisa das bibliotecas nativas do
Pango/GObject/fontconfig para importar (ver `Dockerfile` — `libpango-1.0-0`,
`libpangoft2-1.0-0`, `fonts-dejavu-core`, instaladas via `apt-get`). Isso
funciona nativamente em Linux/Docker (é o ambiente de produção, no Railway).
Se essas bibliotecas estiverem ausentes por algum motivo, o import de
weasyprint é capturado (`src/documento_pdf.py` importa dentro de um
`try/except` na subida) — o app inteiro continua funcionando normalmente, só
a geração do PDF fica indisponível (mesma degradação graciosa que
`CANVA_CLIENT_ID`/`SECRET` ausentes já tinha antes).

> Este fluxo **não usa o Canva** — `src/canva_client.py`/`src/canva_admin.py`
> e as variáveis `CANVA_*` continuam no código só por causa do fluxo manual
> abaixo (que na verdade também não os usa — ver nota no fim da seção 5.2) e
> não foram removidos por baixo risco de mantê-los. Ver seção 2 para o status
> de cada variável `CANVA_*`.

### 5.2 Fluxo MANUAL — conversa com a Claude (Canva)

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

> PENDÊNCIA CONHECIDA (só deste fluxo manual): o fluxo de chat edita o
> template ORIGINAL (sobrescreve a cada ficha). O correto seria **DUPLICAR o
> template por ficha** e trabalhar na cópia. Baixa prioridade — uso pontual,
> só quando o médico quer editar o documento à mão.

> Este fluxo usa o conector Canva da própria conta da Claude (fora deste
> repositório) — não passa por `src/canva_client.py`/`src/canva_admin.py` nem
> pelo token OAuth guardado no banco pelo servidor (esse token existe só por
> herança do antigo fluxo automático via Canva e hoje não é usado por nada).

## 6. Decisões e restrições importantes
- Ferramenta de **apoio**, nunca "fraude confirmada".
- **LGPD — CID (diagnóstico):** decisão de exibir ou não o CID na página pública
  é do **médico, tomada na emissão** (checkbox "Exibir diagnóstico (CID) na
  verificação pública", desmarcado por padrão — ver seção 3), gravada no
  atestado (`exibir_cid`, coluna nova em `atestados`). Quem consulta a página
  pública **não** tem mais controle sobre isso — não existe toggle nem botão de
  revelar na página pública; se o médico não marcou, o CID fica sempre atrás
  de "Protegido por sigilo médico" para todo mundo que consultar aquele
  atestado, sem exceção. Isso substitui uma versão anterior, breve, em que a
  página pública tinha um toggle "Mostrar diagnóstico" acionado por quem
  consultava — o dono do produto decidiu que essa escolha deve ser do médico,
  não de quem verifica.
- **LGPD — CPF:** não vai para o registro do atestado em NENHUM fluxo
  (formulário, API, MCP) — só existe, quando informado, para preencher o PDF
  gerado localmente (seção 5), nunca é persistido em lugar nenhum (nem para
  permitir "tentar novamente" — o dashboard pede o CPF de novo nesse caso).
  A página pública TEM um campo de CPF censurado (formato `***.818.456-**` —
  esconde os 3 primeiros dígitos e os 2 verificadores finais, mostra os 6 do
  meio como referência de conferência), mas como o CPF nunca é persistido,
  esse campo nunca aparece na prática hoje — só existe pronto para o dia em
  que `atestado` eventualmente carregar um `cpf` (ver `_mascarar_cpf()` em
  `app.py`).
- Frente de **Segurança/LGPD CONCLUÍDA** (Partes 1-4): Parte 1 (acesso/login),
  Parte 2 (criptografia em repouso), Parte 3 (auditoria) e Parte 4
  (retenção/exclusão de atestados) — ver seção 3. Não há parte pendente nesta
  frente.
- O PDF gerado é cifrado em repouso (mesma `ENCRYPTION_KEY`) e é apagado junto
  quando o atestado é anonimizado/excluído (Parte 4) — ver seção 5.1.
- Código do QR deve ser **aleatório e imprevisível** (evitar enumeração/vazamento).
- URLs geradas (base do QR/verificação) são **dinâmicas** (baseadas no domínio da
  requisição), para funcionar em localhost e em produção sem hardcode.

## 7. Como rodar localmente (a confirmar no código)
1. Instalar **Python 3.11+** e as dependências: `pip install -r requirements.txt`.
2. (Se o OCR estiver em uso — é secundário/opcional) instalar libs de sistema `tesseract` e `zbar`.
3. Rodar o Streamlit: `streamlit run app.py` (config em `.streamlit/config.toml`, porta 5000).
4. O servidor da API/MCP pode subir junto — verificar o comando/estrutura de execução no projeto.
5. O SQLite é criado/usado localmente. As URLs se adaptam ao localhost automaticamente.
6. Geração de PDF (`src/documento_pdf.py`, weasyprint) precisa das bibliotecas
   nativas do Pango/GObject/fontconfig (mesmas do Dockerfile) para importar —
   em **Linux** normalmente basta `apt-get install libpango-1.0-0
   libpangoft2-1.0-0 fonts-dejavu-core`. Em **Windows** isso é mais difícil:
   weasyprint depende de DLLs do GTK3 que não vêm com `pip install` e cuja
   instalação via instalador oficial exige privilégios de administrador (não
   testado com sucesso nesta sessão, rodando sem admin). Se `import
   weasyprint` falhar, o resto do app funciona normalmente — só a geração do
   PDF fica indisponível (degradação graciosa, sem derrubar o processo; ver
   seção 5.1). Em produção (Docker/Railway) isso não é problema, pois o
   Dockerfile já instala as bibliotecas necessárias.

## 8. Próximos passos / backlog
- **Fluxo Canva manual (chat):** duplicar o template por ficha em vez de editar o
  original — baixa prioridade, uso pontual (ver seção 5.2).
- **Design:** continuar lapidando (as rodadas feitas cobriram ícones, tipografia,
  espaçamento, microinterações, cor, mobile, cabeçalho da verificação e tema
  claro/escuro da página pública).
- **PDF automático:** hoje o status só aparece no dashboard do médico — considerar
  expor também na resposta da API/MCP (ex.: um campo `documento_status`) se fizer
  sentido para quem integra via API/MCP/Make/Zapier.
- **Cidade/UF do CORPO do PDF:** hoje fixa em "Ribeirão Preto, - São Paulo" na
  linha de local/data perto da assinatura (`src/documento_pdf.py`) — diferente
  do endereço do RODAPÉ, que já é por médico (ver seção 5.1). Considerar puxar
  também essa linha do cadastro do médico se algum dia houver mais de uma unidade.

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
