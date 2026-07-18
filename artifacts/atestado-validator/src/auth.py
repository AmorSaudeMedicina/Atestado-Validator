"""
Autenticação com hash de senha (bcrypt) e dois perfis: administrador e médico.

Política de segurança:
- Senhas nunca são armazenadas, exibidas em tela nem registradas em log em
  texto puro — apenas o hash bcrypt (já inclui salt aleatório por conta) é
  gravado no banco. A ÚNICA exceção intencional é a senha inicial do admin
  quando gerada automaticamente (sem ADMIN_INITIAL_PASSWORD definida): ela é
  escrita UMA VEZ no log de inicialização do processo, nunca na tela, e a
  troca é exigida no primeiro login — ver `semear_usuarios_iniciais()`.
- `autenticar()` sempre compara a senha informada com o hash armazenado via
  `bcrypt.checkpw`, nunca por igualdade de string.
- Contas com `ativo=0` nunca autenticam, mesmo com senha correta.
- Após `_MAX_TENTATIVAS_LOGIN` senhas erradas seguidas, a conta fica
  temporariamente bloqueada por `_MINUTOS_BLOQUEIO` minutos (proteção contra
  força bruta) — ver `autenticar()` e `esta_bloqueado()`.
- `validar_senha_forte()` é a política mínima de senha, aplicada pela
  interface sempre que uma senha é criada ou trocada por um humano.

Estrutura pronta para fases futuras (ainda não implementadas):
- Verificação real do CRM junto ao CFM (coluna `crm_verificado_cfm`).
- Confirmação de e-mail (colunas `email`, `email_verificado`).
- Recuperação de senha por e-mail (colunas `reset_senha_token`, `reset_senha_expira`).
  TODO: fica para uma próxima etapa — por enquanto, só o admin redefine a
  senha de um médico pelo painel administrativo já existente.
"""

import logging
import os
import re
import secrets
import string
from typing import Optional

import bcrypt

from src.audit import (
    EVENTO_LOGIN_BLOQUEADO,
    EVENTO_LOGIN_FALHA,
    EVENTO_LOGIN_SUCESSO,
    registrar_evento,
)
from src.database import (
    buscar_usuario_por_login,
    contar_usuarios,
    criar_usuario,
    registrar_tentativa_login_falha,
    resetar_tentativas_login,
    usuario_bloqueado_no_momento,
)

_LOGGER = logging.getLogger("amorsaude.auth")

# ---------------------------------------------------------------------------
# Política de bloqueio por força bruta
# ---------------------------------------------------------------------------
_MAX_TENTATIVAS_LOGIN = 5
_MINUTOS_BLOQUEIO = 15

# ---------------------------------------------------------------------------
# Política de senha forte
# ---------------------------------------------------------------------------
_SENHA_TAMANHO_MINIMO = 10


def validar_senha_forte(senha: str) -> Optional[str]:
    """
    Verifica se `senha` atende à política mínima de segurança para produção:
    pelo menos `_SENHA_TAMANHO_MINIMO` caracteres e pelo menos 3 dos 4 tipos
    de caractere (minúscula, maiúscula, número, símbolo).

    Retorna None se a senha é forte o suficiente, ou uma mensagem em
    português explicando o que falta (pronta para exibir na tela).
    """
    if not senha or len(senha) < _SENHA_TAMANHO_MINIMO:
        return f"A senha deve ter pelo menos {_SENHA_TAMANHO_MINIMO} caracteres."
    tipos_presentes = sum(
        [
            bool(re.search(r"[a-z]", senha)),
            bool(re.search(r"[A-Z]", senha)),
            bool(re.search(r"[0-9]", senha)),
            bool(re.search(r"[^a-zA-Z0-9]", senha)),
        ]
    )
    if tipos_presentes < 3:
        return (
            "A senha deve combinar pelo menos 3 destes 4 tipos: letra minúscula, "
            "letra maiúscula, número e símbolo (ex.: !@#$%)."
        )
    return None


def _gerar_senha_aleatoria_forte(tamanho: int = 20) -> str:
    """Gera uma senha aleatória (segura, alta entropia) que sempre passa em `validar_senha_forte()`."""
    alfabeto = string.ascii_letters + string.digits + "!@#$%&*+-="
    while True:
        candidata = "".join(secrets.choice(alfabeto) for _ in range(tamanho))
        if validar_senha_forte(candidata) is None:
            return candidata


# ---------------------------------------------------------------------------
# Contas iniciais
# ---------------------------------------------------------------------------
# O usuário/nome do admin inicial são fixos (não são segredo); a SENHA nunca
# é fixa no código — ver `semear_usuarios_iniciais()`.
_ADMIN_INICIAL_USUARIO = "admin"
_ADMIN_INICIAL_NOME = "Administrador AmorSaúde"

# Contas de médico de teste — só entram no banco se a variável de ambiente
# SEED_TEST_DATA="true" estiver definida (nunca em produção, ver
# `semear_usuarios_iniciais()`). Senhas fracas aqui são aceitáveis: servem
# apenas para uso local/teste, opt-in, e nunca passam pela validação de
# senha forte da interface (essa validação é sobre o que um humano digita
# num formulário, não sobre dados de seed).
MEDICOS_TESTE: list[dict] = [
    {
        "usuario": "drsilva",
        "senha": "silva123",
        "nome": "Dr. Carlos Silva",
        "crm": "CRM-SP 123456",
        "especialidade": "Clínica Geral",
    },
    {
        "usuario": "dracosta",
        "senha": "costa123",
        "nome": "Dra. Ana Costa",
        "crm": "CRM-RJ 654321",
        "especialidade": "Medicina do Trabalho",
    },
    {
        "usuario": "droliveira",
        "senha": "oliver123",
        "nome": "Dr. Marcos Oliveira",
        "crm": "CRM-MG 987654",
        "especialidade": "Ortopedia",
    },
]


def gerar_hash_senha(senha: str) -> str:
    """Gera um hash bcrypt (com salt aleatório embutido) para a senha informada."""
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verificar_senha(senha: str, hash_armazenado: str) -> bool:
    """Compara uma senha em texto puro com um hash bcrypt já armazenado.

    Qualquer hash ausente/malformado (None, vazio, tipo inesperado, string
    inválida) é tratado como falha de autenticação — nunca levanta exceção.
    """
    if not senha or not hash_armazenado or not isinstance(hash_armazenado, str):
        return False
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), hash_armazenado.encode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        return False


def semear_usuarios_iniciais() -> None:
    """
    Cria a conta de administrador inicial (e, só se pedido explicitamente,
    médicos de teste) na tabela `usuarios`, apenas na primeira execução
    (tabela ainda vazia).

    Idempotente por design: se já existir qualquer conta, não faz nada — isso
    preserva senhas redefinidas e status ativo/inativo alterados posteriormente
    pelo administrador, mesmo que o app reinicie.

    Senha do admin inicial:
    - Se a variável de ambiente ADMIN_INITIAL_PASSWORD estiver definida, ela
      é usada (nunca fica hardcoded no código).
    - Caso contrário, uma senha aleatória forte é gerada e escrita UMA ÚNICA
      VEZ no log de inicialização do processo — nunca na tela do app.
    - Em ambos os casos, a troca de senha é exigida automaticamente no
      primeiro login do admin (`deve_trocar_senha=True`).

    Médicos de teste (MEDICOS_TESTE) só são criados se a variável de ambiente
    SEED_TEST_DATA estiver definida como "true" — nunca em produção por
    padrão, já que essa variável simplesmente não deve existir lá.
    """
    if contar_usuarios() > 0:
        return

    senha_admin = os.environ.get("ADMIN_INITIAL_PASSWORD", "").strip()
    senha_foi_gerada = not senha_admin
    if senha_foi_gerada:
        senha_admin = _gerar_senha_aleatoria_forte()

    criar_usuario(
        usuario=_ADMIN_INICIAL_USUARIO,
        senha_hash=gerar_hash_senha(senha_admin),
        nome=_ADMIN_INICIAL_NOME,
        perfil="admin",
        deve_trocar_senha=True,
    )

    if senha_foi_gerada:
        _LOGGER.warning(
            "Nenhuma variavel de ambiente ADMIN_INITIAL_PASSWORD foi definida — "
            "gerei uma senha aleatoria forte para a conta inicial 'admin'. Essa "
            "senha aparece AQUI NO LOG UMA UNICA VEZ (nunca fica salva em texto "
            "puro, nunca aparece na tela do app) e a troca sera exigida "
            "automaticamente no primeiro login. Copie-a agora:\n"
            "  usuario: %s\n"
            "  senha:   %s",
            _ADMIN_INICIAL_USUARIO,
            senha_admin,
        )
    else:
        _LOGGER.info(
            "Conta inicial 'admin' criada a partir de ADMIN_INITIAL_PASSWORD. "
            "Troca de senha sera exigida no primeiro login."
        )

    if os.environ.get("SEED_TEST_DATA", "").strip().lower() == "true":
        for m in MEDICOS_TESTE:
            criar_usuario(
                usuario=m["usuario"],
                senha_hash=gerar_hash_senha(m["senha"]),
                nome=m["nome"],
                perfil="medico",
                crm=m["crm"],
                especialidade=m["especialidade"],
            )
        _LOGGER.warning(
            "SEED_TEST_DATA=true — %d medico(s) de teste foram criados com "
            "senhas fracas conhecidas. NUNCA defina essa variavel em producao.",
            len(MEDICOS_TESTE),
        )


def esta_bloqueado(usuario: str) -> bool:
    """
    Indica se a conta `usuario` está atualmente sob bloqueio temporário por
    excesso de tentativas de login incorretas — usado pela tela de login só
    para mostrar uma mensagem específica. `autenticar()` já recusa o login
    de qualquer forma enquanto o bloqueio estiver ativo, mesmo sem essa
    checagem prévia (defesa em profundidade).
    """
    registro = buscar_usuario_por_login(usuario)
    if not registro:
        return False
    return usuario_bloqueado_no_momento(registro["id"])


def autenticar(usuario: str, senha: str) -> Optional[dict]:
    """
    Verifica usuário/senha comparando com o hash bcrypt armazenado.

    Retorna os dados da conta (sem a senha/hash) em caso de sucesso, ou None
    se o usuário não existir, a conta estiver desativada, a conta estiver
    temporariamente bloqueada por excesso de tentativas, ou a senha estiver
    incorreta. A senha informada nunca é registrada em log nem incluída no
    valor de retorno.

    Cada senha incorreta soma uma tentativa falha; ao atingir
    `_MAX_TENTATIVAS_LOGIN`, a conta é bloqueada por `_MINUTOS_BLOQUEIO`
    minutos. Um login bem-sucedido zera o contador.
    """
    registro = buscar_usuario_por_login(usuario)
    if not registro:
        registrar_evento(EVENTO_LOGIN_FALHA, ator_usuario=usuario, detalhe="usuario inexistente")
        return None
    if not registro["ativo"]:
        registrar_evento(
            EVENTO_LOGIN_FALHA, ator_usuario=usuario, ator_perfil=registro["perfil"], detalhe="conta desativada"
        )
        return None
    if usuario_bloqueado_no_momento(registro["id"]):
        registrar_evento(
            EVENTO_LOGIN_FALHA, ator_usuario=usuario, ator_perfil=registro["perfil"], detalhe="conta bloqueada"
        )
        return None
    if not verificar_senha(senha, registro["senha_hash"]):
        cruzou_limite = registrar_tentativa_login_falha(registro["id"], _MAX_TENTATIVAS_LOGIN, _MINUTOS_BLOQUEIO)
        registrar_evento(EVENTO_LOGIN_FALHA, ator_usuario=usuario, ator_perfil=registro["perfil"])
        if cruzou_limite:
            registrar_evento(EVENTO_LOGIN_BLOQUEADO, ator_usuario=usuario, ator_perfil=registro["perfil"])
        return None
    resetar_tentativas_login(registro["id"])
    registrar_evento(EVENTO_LOGIN_SUCESSO, ator_usuario=usuario, ator_perfil=registro["perfil"])
    return {k: v for k, v in registro.items() if k != "senha_hash"}
