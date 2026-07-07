"""
risk_report.py — Cálculo de pontuação de risco e geração do relatório.

Responsabilidade futura:
  - Consolidar os resultados de source_check, ocr, parser e validators.
  - Calcular uma pontuação de risco ponderada com base nos sinais coletados
    (QR inválido, campos ausentes, inconsistências de data, emissor
    desconhecido, etc.).
  - Classificar o atestado em faixas de risco (ex.: Baixo / Médio / Alto).
  - Montar o relatório final com evidências objetivas para apresentação
    ao analista humano (RH/Auditoria).

IMPORTANTE: O sistema é uma ferramenta de APOIO. O relatório deve deixar
explícito que a decisão final cabe ao analista responsável, em conformidade
com a LGPD e as políticas internas da organização.
"""
