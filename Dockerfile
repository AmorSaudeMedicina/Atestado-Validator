# Imagem de produção do "Validador/Emissor de Atestados" (AmorSaúde) para o Railway.
#
# Este repositório é um monorepo (pnpm workspace com vários artefatos); o app
# de verdade que roda em produção mora em artifacts/atestado-validator/. Este
# Dockerfile copia apenas essa pasta, então os outros artefatos (React/Vite,
# api-server em Node etc.) não entram nesta imagem.
#
# server.py sobe UM único processo (Streamlit + API REST + endpoint do QR +
# MCP/OAuth) na porta indicada pela variável de ambiente PORT, escutando em
# 0.0.0.0 — é o que o Railway espera de qualquer serviço web.
FROM python:3.11-slim

WORKDIR /app

COPY artifacts/atestado-validator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY artifacts/atestado-validator/ ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "server.py"]
