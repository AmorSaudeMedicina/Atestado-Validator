#!/usr/bin/env python3
"""
Limpeza de dados de teste — Validador/Emissor de Atestados (AmorSaúde).

O QUE FAZ (após confirmação explícita, tudo IRREVERSÍVEL):
  a) Apaga TODOS os atestados do banco (tabela `atestados`).
  b) Apaga TODOS os PDFs gerados em DATA_DIR/documentos/ (arquivos .pdf.enc/.pdf)
     e limpa a tabela `documentos_atestado` que os referenciava (senão o
     dashboard mostraria "Baixar PDF" apontando para arquivos que já não existem).
  c) Apaga TODOS os eventos da trilha de auditoria (tabela `eventos_auditoria`).
  d) Apaga apenas os médicos de TESTE: dracosta (Dra. Ana Costa) e
     droliveira (Dr. Marcos Oliveira).
     MANTÉM intactos: admin, Daniel e drsilva (Dr. Carlos Silva).

Não precisa da ENCRYPTION_KEY: apagar linhas não exige descriptografar nada.
Usa só a biblioteca padrão do Python (sqlite3), então roda em qualquer ambiente
onde o app roda — inclusive o Console do Railway.

O caminho do banco e da pasta de documentos é resolvido EXATAMENTE como o app
resolve (src/database.py e src/documento_pdf.py):
  - Banco:      DATABASE_PATH  ->  DATA_DIR/atestados.db  ->  <app>/data/atestados.db
  - Documentos: DATA_DIR/documentos                       ->  <app>/data/documentos

Como rodar:
    python scripts/limpar_dados_teste.py
"""

import os
import sqlite3
import sys
from pathlib import Path

# Raiz do app = pasta que contém src/, server.py, assets/ (este script vive em
# <app>/scripts/, então subir um nível chega na raiz). Usado só para o caminho
# padrão de desenvolvimento, quando não há DATABASE_PATH nem DATA_DIR definidos
# — o mesmo que src/database.py faz com Path(__file__).parent.parent.
_APP_ROOT = Path(__file__).resolve().parent.parent

# Médicos de TESTE a remover. Os demais (admin, Daniel, drsilva) são preservados.
USUARIOS_TESTE_PARA_REMOVER = ["dracosta", "droliveira"]


def caminho_banco() -> Path:
    """Mesma resolução de src/database.py."""
    if os.environ.get("DATABASE_PATH"):
        return Path(os.environ["DATABASE_PATH"])
    base = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else _APP_ROOT / "data"
    return base / "atestados.db"


def diretorio_documentos() -> Path:
    """Mesma resolução de src/documento_pdf.py._diretorio_documentos()."""
    base = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else _APP_ROOT / "data"
    return base / "documentos"


def _contar(conn: sqlite3.Connection, tabela: str, where: str = "", params: tuple = ()) -> int:
    """Conta linhas de uma tabela; devolve 0 se a tabela ainda nem existir."""
    try:
        sql = f"SELECT COUNT(*) FROM {tabela}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _apagar(conn: sqlite3.Connection, tabela: str, where: str = "", params: tuple = ()) -> int:
    """Apaga linhas de uma tabela; ignora silenciosamente se a tabela não existir."""
    try:
        sql = f"DELETE FROM {tabela}"
        if where:
            sql += f" WHERE {where}"
        cur = conn.execute(sql, params)
        return cur.rowcount if cur.rowcount is not None else 0
    except sqlite3.OperationalError:
        return 0


def apagar_pdfs(pasta: Path) -> int:
    """Apaga os PDFs gerados na pasta de documentos. Devolve quantos foram apagados."""
    if not pasta.exists():
        return 0
    apagados = 0
    for arquivo in list(pasta.glob("*.pdf.enc")) + list(pasta.glob("*.pdf")):
        try:
            arquivo.unlink()
            apagados += 1
        except OSError as erro:
            print(f"  ! Não consegui apagar {arquivo.name}: {erro}")
    return apagados


def main() -> int:
    db = caminho_banco()
    docs = diretorio_documentos()

    print("=" * 64)
    print("LIMPEZA DE DADOS DE TESTE — Validador de Atestados (AmorSaúde)")
    print("=" * 64)
    print(f"Banco de dados : {db}")
    print(f"Pasta de PDFs  : {docs}")
    print()

    if not db.exists():
        print(f"ERRO: banco não encontrado em {db}")
        print("Confirme a variável DATA_DIR/DATABASE_PATH deste ambiente.")
        return 1

    # Prévia do que será apagado, para o operador conferir ANTES de confirmar.
    with sqlite3.connect(db) as conn:
        n_atestados = _contar(conn, "atestados")
        n_docs_reg = _contar(conn, "documentos_atestado")
        n_auditoria = _contar(conn, "eventos_auditoria")
        marcadores = ",".join("?" for _ in USUARIOS_TESTE_PARA_REMOVER)
        n_medicos = _contar(
            conn,
            "usuarios",
            f"usuario IN ({marcadores})",
            tuple(USUARIOS_TESTE_PARA_REMOVER),
        )
    n_pdfs = len(list(docs.glob("*.pdf.enc")) + list(docs.glob("*.pdf"))) if docs.exists() else 0

    print("Será apagado:")
    print(f"  - Atestados no banco .................. {n_atestados}")
    print(f"  - PDFs em documentos/ ................. {n_pdfs}")
    print(f"  - Registros de documento (tabela) ..... {n_docs_reg}")
    print(f"  - Eventos de auditoria ................ {n_auditoria}")
    print(f"  - Médicos de teste ({', '.join(USUARIOS_TESTE_PARA_REMOVER)}) .... {n_medicos}")
    print()
    print("Serão MANTIDOS: admin, Daniel, drsilva (Dr. Carlos Silva).")
    print()

    resposta = input("Tem certeza? Esta ação é IRREVERSÍVEL. Digite 'CONFIRMAR' para prosseguir. ").strip()
    if resposta != "CONFIRMAR":
        print("\nCancelado. Nada foi apagado.")
        return 1

    print("\nExecutando limpeza...\n")

    with sqlite3.connect(db) as conn:
        # (a) atestados
        del_atestados = _apagar(conn, "atestados")
        # (b) registros de documento + arquivos PDF
        del_docs_reg = _apagar(conn, "documentos_atestado")
        # (c) eventos de auditoria
        del_auditoria = _apagar(conn, "eventos_auditoria")
        # (d) médicos de teste
        del_medicos = _apagar(
            conn,
            "usuarios",
            f"usuario IN ({marcadores})",
            tuple(USUARIOS_TESTE_PARA_REMOVER),
        )
        conn.commit()

    # Arquivos PDF em disco (fora do banco, feito depois do commit).
    del_pdfs = apagar_pdfs(docs)

    print("-" * 64)
    print("RESUMO DO QUE FOI APAGADO")
    print("-" * 64)
    print(f"  Atestados apagados ................... {del_atestados}")
    print(f"  PDFs apagados (arquivos) ............. {del_pdfs}")
    print(f"  Registros de documento apagados ..... {del_docs_reg}")
    print(f"  Eventos de auditoria apagados ....... {del_auditoria}")
    print(f"  Médicos de teste apagados ........... {del_medicos}  ({', '.join(USUARIOS_TESTE_PARA_REMOVER)})")
    print("-" * 64)
    print("Concluído. Contas mantidas: admin, Daniel, drsilva.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
