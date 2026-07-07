# Validador de Atestados

Ferramenta de apoio à análise de atestados (médicos, odontológicos e de comparecimento). Processa documentos via QR Code e OCR para auxiliar equipes de RH/Auditoria — nunca emite veredito final.

## Run & Operate

- `cd artifacts/atestado-validator && streamlit run app.py` — rodar o app Streamlit (porta 5000)
- Workflow configurado: **"Validador de Atestados"**
- Dependência de sistema: `tesseract` (instalado via Nix)
- Dependências Python: ver `artifacts/atestado-validator/requirements.txt`

## Stack

- Python 3.11 + Streamlit
- OCR: pytesseract (wrapper do Tesseract)
- QR Code: pyzbar + opencv-python-headless
- Imagens: Pillow
- HTTP: requests

## Where things live

- `artifacts/atestado-validator/app.py` — ponto de entrada do Streamlit
- `artifacts/atestado-validator/src/` — módulos de validação (esqueleto, a implementar)
- `artifacts/atestado-validator/.streamlit/config.toml` — configuração do servidor
- `artifacts/atestado-validator/samples/` — arquivos de teste sintéticos (NUNCA atestados reais)
- `artifacts/atestado-validator/README.md` — documentação do projeto e fases

## Architecture decisions

- Validação principal via **QR Code** (consulta à fonte emissora); OCR é conferência cruzada e fallback.
- Sistema de **apoio** humano — sem veredito automatizado de fraude.
- Dados de saúde tratados como sensíveis (LGPD art. 11): sem persistência sem consentimento.
- Módulos desacoplados para permitir desenvolvimento por fases independentes.

## Product

Fase 1 (atual): esqueleto com upload de arquivo. Fases seguintes: leitura QR → consulta fonte → OCR → parsing → validação de campos → relatório de risco.

## User preferences

- App em Python/Streamlit — NÃO usar React ou JavaScript para este projeto.
- Desenvolvimento incremental por fases.
- Nome do app: "atestado-validator".

## Gotchas

- O `config.toml` do Streamlit já está configurado para `port=5000` e `address=0.0.0.0` — não alterar sem atualizar o workflow.
- `samples/` é para arquivos sintéticos apenas — avisar usuário sobre LGPD.
- Tesseract instalado via Nix (`tesseract`); pytesseract aponta automaticamente para ele.

## Pointers

- Ver `artifacts/atestado-validator/README.md` para diagrama de fluxo e tabela de fases.
