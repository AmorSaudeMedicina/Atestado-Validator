"""
Geração de QR Code para o link de verificação do atestado.
"""

import io
import qrcode
from PIL import Image


def gerar_qr(url: str, tamanho_caixa: int = 10, borda: int = 4) -> bytes:
    """
    Gera um QR Code apontando para `url` e retorna os bytes PNG.

    Args:
        url: URL completa de verificação (ex.: https://dominio/?codigo=XYZ)
        tamanho_caixa: pixels por módulo do QR.
        borda: largura da borda em módulos.

    Returns:
        bytes PNG da imagem.
    """
    qr = qrcode.QRCode(
        version=None,  # auto-detecta o menor tamanho suficiente
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=tamanho_caixa,
        border=borda,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img: Image.Image = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
