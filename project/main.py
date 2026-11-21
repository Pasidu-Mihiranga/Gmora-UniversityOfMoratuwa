"""SuwaPath entry point.

Usage:
    python main.py api [--host 127.0.0.1] [--port 8000] [--reload]
    python main.py migrate [revision]

Further commands (worker, agents, migrate, seed, eval, openapi, clock, mcp) are added as their
components land. See docs/specs/01-architecture.md section 7.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence


def _cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "suwapath.platform.http.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), args.revision)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="SuwaPath command line")
    sub = parser.add_subparsers(dest="command", required=True)

    api = sub.add_parser("api", help="run the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--reload", action="store_true", help="reload on code changes (development)")
    api.set_defaults(handler=_cmd_api)

    migrate = sub.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("revision", nargs="?", default="head")
    migrate.set_defaults(handler=_cmd_migrate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
