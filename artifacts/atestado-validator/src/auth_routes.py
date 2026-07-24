"""
auth_routes.py — Rota HTTP para o handoff do "Lembrar de mim" (define o
cookie httpOnly de verdade). Ver src/lembrar_me.py para o fluxo completo e
o porquê desta rota existir separada do ciclo normal do Streamlit.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from src.lembrar_me import MAX_AGE_COOKIE_SEGUNDOS, NOME_COOKIE, trocar_handoff_por_cookie
from src.urls import esta_em_https, url_base


async def lembrar_me_handoff(request: Request) -> Response:
    """
    GET /auth/lembrar-me?token=... — o navegador chega aqui automaticamente
    (redirecionamento de uma fração de segundo, disparado pelo próprio
    app.py) logo após um login bem-sucedido com "Lembrar de mim" marcado.

    Troca o token de handoff (uso único, 60s) pelo cookie httpOnly de longa
    duração e volta para a página principal. Se o token vier ausente,
    inválido ou expirado, apenas volta para a página principal sem definir
    cookie nenhum — o médico já está logado normalmente (sessão comum
    dentro do Streamlit), só não ganha a sessão longa.
    """
    token_handoff = request.query_params.get("token", "")
    destino = url_base(request)

    resposta = RedirectResponse(url=destino, status_code=302)

    if token_handoff:
        token_cookie = trocar_handoff_por_cookie(token_handoff)
        if token_cookie:
            resposta.set_cookie(
                NOME_COOKIE,
                token_cookie,
                max_age=MAX_AGE_COOKIE_SEGUNDOS,
                path="/",
                httponly=True,
                secure=esta_em_https(request),
                samesite="lax",
            )

    return resposta
