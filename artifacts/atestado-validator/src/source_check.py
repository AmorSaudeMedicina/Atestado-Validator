"""
source_check.py — Validação do atestado na fonte via URL do QR Code.

Responsabilidade futura:
  - Receber a URL decodificada pelo qr_reader.
  - Verificar se o domínio está na lista branca de issuers.py.
  - Realizar a requisição HTTP à fonte emissora e interpretar a resposta
    (documento válido, revogado, não encontrado, etc.).
  - Retornar um dicionário estruturado com o resultado da consulta e
    metadados relevantes para o relatório de risco.
"""
