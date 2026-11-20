"""Comandos administrativos.

Uso: python -m app.cli create-user --email voce@exemplo.com

A senha é lida de forma interativa (ou da variável JOBPILOT_USER_PASSWORD) para
não ficar registrada no histórico do shell.
"""

import argparse
import getpass
import os
import sys

from app.core.errors import ConflictError
from app.db.session import get_session_factory
from app.services import auth_service


def create_user_command(email: str) -> int:
    password = os.environ.get("JOBPILOT_USER_PASSWORD") or getpass.getpass("Senha: ")
    with get_session_factory()() as session:
        try:
            user = auth_service.create_user(session, email, password)
        except (ValueError, ConflictError) as exc:
            print(f"Erro: {exc}", file=sys.stderr)
            return 1
    print(f"Usuário criado: {user.email} ({user.id})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subcommands = parser.add_subparsers(dest="command", required=True)
    create_user = subcommands.add_parser("create-user", help="Cria um usuário da API.")
    create_user.add_argument("--email", required=True)

    args = parser.parse_args()
    if args.command == "create-user":
        return create_user_command(args.email)
    return 1


if __name__ == "__main__":
    sys.exit(main())
