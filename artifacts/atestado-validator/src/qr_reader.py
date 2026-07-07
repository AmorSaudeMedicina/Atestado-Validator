"""
qr_reader.py — Detecção e leitura do QR Code do atestado.

Responsabilidade futura:
  - Receber a imagem ou PDF convertido em imagem.
  - Localizar e decodificar o QR Code usando pyzbar / opencv.
  - Retornar a URL ou payload contido no QR Code para que
    source_check.py possa validar a autenticidade na fonte emissora.
  - Retornar None (sem erro fatal) caso o documento não possua QR Code,
    permitindo o fallback para validação via OCR.
"""
