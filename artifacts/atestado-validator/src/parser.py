"""
parser.py — Localização e extração de campos no texto obtido pelo OCR.

Responsabilidade futura:
  - Receber o texto bruto produzido por ocr.py.
  - Aplicar expressões regulares e heurísticas para identificar e extrair
    campos estruturados: nome do paciente, CPF, CRM/CRO, CID, datas
    (emissão, validade, afastamento), assinatura digital, etc.
  - Retornar um dicionário padronizado de campos que será consumido por
    validators.py e risk_report.py.
"""
