# Validador de Atestados

Ferramenta de **apoio** à análise de atestados médicos, odontológicos e de comparecimento.  
Destinada a equipes de RH e auditoria — **não emite veredito final de fraude**.  
A decisão cabe sempre ao analista humano responsável.

---

## ⚠️ Aviso Legal e LGPD

Este sistema processa dados sensíveis de saúde (art. 11 da LGPD — Lei 13.709/2018).  
- **Não armazene** atestados reais sem consentimento do titular.  
- A pasta `samples/` é destinada **exclusivamente a arquivos de teste sintéticos**.  
- **NUNCA coloque atestados reais de pessoas em `samples/`.**  
- Em produção, adote criptografia em repouso, controle de acesso e política de retenção.

---

## Fluxo de Validação

```
Upload do Atestado
       │
       ▼
  [qr_reader]  ──→ QR Code encontrado? ──Sim──→ [source_check] ──→ Consulta na fonte emissora
       │                                                                     │
      Não                                                                    │
       │                                                                     │
       ▼                                                                     ▼
   [ocr]  ──→ Texto extraído  ──→  [parser]  ──→  [validators]  ──→  [risk_report]
```

A validação principal é feita via **QR Code** (consulta à fonte emissora).  
O **OCR** (Tesseract) serve como conferência cruzada e fallback quando o QR está ausente.

---

## Estrutura do Projeto

```
atestado-validator/
├── app.py                  # Ponto de entrada Streamlit
├── requirements.txt        # Dependências Python
├── .streamlit/
│   └── config.toml         # Configuração do servidor Streamlit
├── samples/
│   └── .gitkeep            # Pasta para arquivos de teste sintéticos (NÃO use atestados reais)
├── src/
│   ├── __init__.py
│   ├── qr_reader.py        # Detectar e ler QR Code (pyzbar + opencv)
│   ├── issuers.py          # Lista branca de emissores confiáveis
│   ├── source_check.py     # Validar na fonte via URL do QR
│   ├── ocr.py              # Extrair texto com Tesseract (pytesseract)
│   ├── parser.py           # Localizar campos no texto extraído
│   ├── validators.py       # Regras de CPF, CNPJ, CRM/CRO, CID, datas
│   └── risk_report.py      # Calcular risco e montar relatório
└── README.md
```

---

## Dependências de Sistema

| Pacote        | Finalidade                          |
|---------------|-------------------------------------|
| `tesseract`   | Motor OCR (instalado via Nix/apt)   |

---

## Dependências Python

| Pacote                    | Finalidade                              |
|---------------------------|-----------------------------------------|
| `streamlit`               | Interface web                           |
| `pytesseract`             | Wrapper Python para o Tesseract OCR     |
| `pyzbar`                  | Leitura de QR Codes e códigos de barras |
| `opencv-python-headless`  | Pré-processamento de imagens            |
| `pillow`                  | Manipulação de imagens (PIL)            |
| `requests`                | Consultas HTTP à fonte emissora         |

---

## Fases de Desenvolvimento

| Fase | Descrição                                                              | Status       |
|------|------------------------------------------------------------------------|--------------|
| 1    | Esqueleto do projeto — estrutura de pastas, arquivos e upload de arquivo | ✅ Concluída |
| 2    | Leitura do QR Code e consulta à fonte emissora (`qr_reader` + `source_check`) | 🔜 Pendente |
| 3    | Extração de texto por OCR (`ocr` + `parser`)                           | 🔜 Pendente |
| 4    | Validação de campos: CPF, CNPJ, CRM/CRO, CID, datas (`validators`)    | 🔜 Pendente |
| 5    | Relatório de risco e interface de resultado (`risk_report`)            | 🔜 Pendente |
| 6    | Testes, auditoria de segurança e adequação LGPD                        | 🔜 Pendente |

---

## Como executar (Replit)

O app é iniciado automaticamente pelo workflow configurado.  
Para rodar manualmente (só a interface Streamlit, sem API/MCP):

```bash
cd artifacts/atestado-validator
streamlit run app.py --server.port 5000
```

## Como executar em produção (Railway)

Em produção, o ponto de entrada é `server.py`, não `app.py` — ele sobe a
interface Streamlit e as rotas HTTP (API REST, imagem do QR, MCP/OAuth) no
MESMO processo e MESMA porta pública:

```bash
cd artifacts/atestado-validator
python server.py
```

`server.py` lê a porta da variável de ambiente `PORT` (padrão 5000 se não
definida) e escuta em `0.0.0.0`. O deploy no Railway usa o `Dockerfile` na
raiz do repositório — ver `railway.toml` e a seção "Deploy no Railway" no
`CLAUDE.md`/mensagem do assistente para o caminho do volume persistente do
banco SQLite (`DATA_DIR`).
