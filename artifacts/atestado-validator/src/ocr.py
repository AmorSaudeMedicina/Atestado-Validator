"""
ocr.py — Extração de texto do documento via OCR (Tesseract).

Responsabilidade futura:
  - Receber o arquivo (imagem ou PDF convertido em imagem).
  - Pré-processar a imagem com opencv (binarização, remoção de ruído)
    para melhorar a acurácia do Tesseract.
  - Executar o pytesseract em português (por=por) e retornar o texto bruto.
  - Atuar como conferência cruzada dos dados obtidos via QR Code e como
    fallback quando o QR Code está ausente ou ilegível.

Nota: dados extraídos pelo OCR são tratados como sensíveis (LGPD art. 11)
e não devem ser persistidos sem consentimento explícito.
"""
