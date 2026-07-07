"""
Autenticação de médicos — PROTÓTIPO APENAS.

As credenciais estão em texto puro e hardcoded intencionalmente para facilitar
testes. Segurança real (hash de senha, JWT, MFA) será implementada em fase
posterior antes de qualquer uso com dados reais.
"""

from typing import Optional

# Médicos de teste — exibidos na tela de login para facilitar os testes
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

# Índice por usuário para lookup rápido
_INDICE: dict[str, dict] = {m["usuario"]: m for m in MEDICOS_TESTE}


def autenticar(usuario: str, senha: str) -> Optional[dict]:
    """
    Verifica credenciais e retorna os dados do médico ou None.

    ⚠️ PROTÓTIPO: comparação em texto puro, sem hash.
    """
    medico = _INDICE.get(usuario)
    if medico and medico["senha"] == senha:
        # Retorna cópia sem a senha
        return {k: v for k, v in medico.items() if k != "senha"}
    return None
