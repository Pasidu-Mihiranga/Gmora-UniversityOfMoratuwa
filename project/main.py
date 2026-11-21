"""SuwaPath entry point.

Usage:
    python main.py api [--host 127.0.0.1] [--port 8000] [--reload]
    python main.py migrate [revision]
    python main.py worker
    python main.py clock show | advance 4h

Further commands (worker, agents, migrate, seed, eval, openapi, clock, mcp) are added as their
components land. See docs/specs/01-architecture.md section 7.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import timedelta


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


def _cmd_worker(args: argparse.Namespace) -> int:
    import asyncio

    from suwapath.platform.clock import Clock, SystemClock
    from suwapath.platform.clock_store import SharedClock
    from suwapath.platform.config import get_settings
    from suwapath.platform.db import Database
    from suwapath.platform.events.subscribers import SubscriberRegistry
    from suwapath.platform.events.worker import Worker, install_signal_handlers
    from suwapath.platform.logging import configure_logging
    from suwapath.platform.uow import UnitOfWorkFactory

    configure_logging()
    settings = get_settings()

    async def run() -> None:
        db = Database(settings.database_url)
        shared = SharedClock() if settings.dev_tools_enabled else None
        clock: Clock = shared if shared is not None else SystemClock()
        # Module subscribers are registered by bootstrap.py once the modules exist (W0.8).
        worker = Worker(
            db, UnitOfWorkFactory(db, clock), SubscriberRegistry(), settings.database_url, shared
        )
        install_signal_handlers(worker)
        try:
            await worker.run()
        finally:
            await db.dispose()

    asyncio.run(run())
    return 0


def _parse_duration(text: str) -> timedelta:
    units = {"m": "minutes", "h": "hours", "d": "days"}
    number, unit = text[:-1], text[-1]
    if unit not in units or not number.isdigit():
        raise argparse.ArgumentTypeError("use a number and a unit, for example 30m, 4h or 2d")
    return timedelta(**{units[unit]: int(number)})


def _cmd_clock(args: argparse.Namespace) -> int:
    import asyncio

    from suwapath.platform.clock_store import SharedClock
    from suwapath.platform.config import get_settings
    from suwapath.platform.db import Database

    settings = get_settings()
    if not settings.dev_tools_enabled:
        print("The simulated clock is disabled in production (EVT-18).")
        return 2

    async def run() -> None:
        db = Database(settings.database_url, pool_size=1)
        clock = SharedClock()
        async with db.sessions() as session, session.begin():
            now = await clock.advance(session, args.duration) if args.action == "advance" else None
            if now is None:
                await clock.refresh(session)
                now = clock.now()
        print(f"Simulated time: {now.isoformat()} (offset {clock.offset})")
        await db.dispose()

    asyncio.run(run())
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

    worker = sub.add_parser("worker", help="deliver events and fire timers")
    worker.set_defaults(handler=_cmd_worker)

    clock = sub.add_parser("clock", help="show or advance the simulated clock (development, demo)")
    clock.add_argument("action", choices=["show", "advance"])
    clock.add_argument("duration", nargs="?", type=_parse_duration, default=None)
    clock.set_defaults(handler=_cmd_clock)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
