"""
validators.py — Regras de validação para campos individuais do atestado.

Responsabilidade futura:
  - CPF: validar dígitos verificadores.
  - CNPJ: validar dígitos verificadores da clínica/empresa emissora.
  - CRM / CRO: verificar formato e, opcionalmente, consultar API dos
    Conselhos para confirmar o registro do profissional.
  - CID-10: verificar se o código existe na tabela CID oficial.
  - Datas: verificar coerência entre data de emissão, data do atendimento
    e período de afastamento (ex.: afastamento retroativo suspeito).
  - Retornar para cada campo: valor extraído, status (válido/inválido/não
    encontrado) e mensagem descritiva para o relatório.
"""
