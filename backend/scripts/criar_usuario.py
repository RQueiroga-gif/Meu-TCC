"""Cria ou atualiza um usuario do sistema.

Uso:
    python -m scripts.criar_usuario --login ronan --nome "Ronan Rocha Queiroga" --perfil gestor
    (a senha e pedida no terminal; ou informe --senha)

O login pode ser um e-mail ou um nome de usuario. Senhas com menos de 8
caracteres sao recusadas, salvo com --permitir-senha-curta (apenas para
ambiente de desenvolvimento local).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import config  # noqa: E402
from app.core import auditoria  # noqa: E402
from app.database import aplicar_esquema, sessao_escopo  # noqa: E402
from app.models import Usuario  # noqa: E402
from app.security import gerar_hash_senha  # noqa: E402


def main() -> int:
    analisador = argparse.ArgumentParser(description="Cria ou atualiza um usuário")
    analisador.add_argument("--login", required=True, help="e-mail ou nome de usuário")
    analisador.add_argument("--nome", required=True)
    analisador.add_argument("--perfil", choices=["demandante", "gestor"], default="demandante")
    analisador.add_argument("--unidade", default=None)
    analisador.add_argument("--senha", help="se omitida, é pedida no terminal")
    analisador.add_argument(
        "--permitir-senha-curta",
        action="store_true",
        help="aceita senha com menos de 8 caracteres (só desenvolvimento local)",
    )
    argumentos = analisador.parse_args()

    login = argumentos.login.strip().lower()
    senha = argumentos.senha or getpass.getpass("Senha: ")
    if len(senha) < 8:
        if not argumentos.permitir_senha_curta:
            print("Senha com menos de 8 caracteres. Use --permitir-senha-curta para forçar.")
            return 1
        senha_hash = bcrypt.hashpw(
            senha.encode("utf-8"), bcrypt.gensalt(rounds=config.BCRYPT_RODADAS)
        ).decode("utf-8")
    else:
        senha_hash = gerar_hash_senha(senha)

    aplicar_esquema()
    with sessao_escopo() as sessao:
        usuario = sessao.execute(select(Usuario).where(Usuario.email == login)).scalar_one_or_none()
        acao = "USUARIO_ATUALIZADO" if usuario else "USUARIO_CRIADO"
        if usuario is None:
            usuario = Usuario(email=login)
            sessao.add(usuario)
        usuario.nome = argumentos.nome
        usuario.perfil = argumentos.perfil
        usuario.unidade = argumentos.unidade
        usuario.senha_hash = senha_hash
        usuario.ativo = True
        sessao.flush()
        auditoria.registrar(
            sessao,
            id_usuario=usuario.id_usuario,
            acao=acao,
            detalhe={"login": login, "perfil": argumentos.perfil},
        )

    print(f"{'Atualizado' if acao == 'USUARIO_ATUALIZADO' else 'Criado'}: {login} ({argumentos.perfil})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
