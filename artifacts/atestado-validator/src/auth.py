"""
Autenticação com hash de senha (bcrypt) e dois perfis: administrador e médico.

Política de segurança:
- Senhas nunca são armazenadas nem registradas em log em texto puro — apenas
  o hash bcrypt (já inclui salt aleatório por conta) é gravado no banco.
- `autenticar()` sempre compara a senha informada com o hash armazenado via
  `bcrypt.checkpw`, nunca por igualdade de string.
- Contas com `ativo=0` nunca autenticam, mesmo com senha correta.

Estrutura pronta para fases futuras (ainda não implementadas):
- Verificação real do CRM junto ao CFM (coluna `crm_verificado_cfm`).
- Confirmação de e-mail (colunas `email`, `email_verificado`).
- Recuperação de senha por e-mail (colunas `reset_senha_token`, `reset_senha_expira`).
"""

from typing import Optional

import bcrypt

from src.database import (
    buscar_usuario_por_login,
    contar_usuarios,
    criar_usuario,
)

# ---------------------------------------------------------------------------
# Credenciais iniciais do protótipo
# ---------------------------------------------------------------------------
# Exibidas na tela de login apenas para viabilizar o primeiro acesso em fase
# de testes. As senhas abaixo só existem em texto puro aqui, neste módulo —
# no banco elas são sempre gravadas como hash bcrypt (ver `semear_usuarios_iniciais`).
# Antes de qualquer uso com dados reais, o administrador inicial deve trocar
# essa senha pelo painel (redefinição de senha).

ADMIN_INICIAL: dict = {
    "usuario": "admin",
    "senha": "AdminAmor@2026",
    "nome": "Administrador AmorSaúde",
}

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
    Cria a conta de administrador inicial e migra os médicos de teste para a
    tabela `usuarios`, apenas na primeira execução (tabela ainda vazia).

    Idempotente por design: se já existir qualquer conta, não faz nada — isso
    preserva senhas redefinidas e status ativo/inativo alterados posteriormente
    pelo administrador, mesmo que o app reinicie.
    """
    if contar_usuarios() > 0:
        return

    criar_usuario(
        usuario=ADMIN_INICIAL["usuario"],
        senha_hash=gerar_hash_senha(ADMIN_INICIAL["senha"]),
        nome=ADMIN_INICIAL["nome"],
        perfil="admin",
    )
    for m in MEDICOS_TESTE:
        criar_usuario(
            usuario=m["usuario"],
            senha_hash=gerar_hash_senha(m["senha"]),
            nome=m["nome"],
            perfil="medico",
            crm=m["crm"],
            especialidade=m["especialidade"],
        )


def autenticar(usuario: str, senha: str) -> Optional[dict]:
    """
    Verifica usuário/senha comparando com o hash bcrypt armazenado.

    Retorna os dados da conta (sem a senha/hash) em caso de sucesso, ou None
    se o usuário não existir, a conta estiver desativada, ou a senha estiver
    incorreta. A senha informada nunca é registrada em log nem incluída no
    valor de retorno.
    """
    registro = buscar_usuario_por_login(usuario)
    if not registro:
        return None
    if not registro["ativo"]:
        return None
    if not verificar_senha(senha, registro["senha_hash"]):
        return None
    return {k: v for k, v in registro.items() if k != "senha_hash"}
