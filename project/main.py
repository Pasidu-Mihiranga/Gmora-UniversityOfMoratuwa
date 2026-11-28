"""SuwaPath entry point.

Usage:
    python main.py api [--host 127.0.0.1] [--port 8000] [--reload]
    python main.py migrate [revision]
    python main.py worker
    python main.py clock show | advance 4h
    python main.py openapi [--out path]
    python main.py seed generate [--seed 42] [--out path]

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
        "suwapath.bootstrap:create_api",
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

    from suwapath.bootstrap import build_container
    from suwapath.platform.events.worker import Worker, install_signal_handlers
    from suwapath.platform.logging import configure_logging

    configure_logging()

    async def run() -> None:
        container = build_container()
        worker = Worker(
            container.db,
            container.uow_factory,
            container.subscribers,
            container.settings.database_url,
            container.shared_clock,
        )
        install_signal_handlers(worker)
        try:
            await worker.run()
        finally:
            await container.db.dispose()

    asyncio.run(run())
    return 0


def _cmd_agents(args: argparse.Namespace) -> int:
    import asyncio

    from suwapath.bootstrap import build_container, run_agent_worker
    from suwapath.platform.logging import configure_logging

    configure_logging()

    async def run() -> None:
        container = build_container()
        try:
            await run_agent_worker(container)
        finally:
            await container.db.dispose()

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


def _cmd_openapi(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from suwapath.bootstrap import create_api

    schema = create_api().openapi()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.out} ({len(schema['paths'])} paths)")
    return 0


def _cmd_seed_accounts(args: argparse.Namespace) -> int:
    import asyncio

    from suwapath.bootstrap import build_container, seed_demo_accounts

    async def run() -> list[str]:
        container = build_container()
        try:
            return await seed_demo_accounts(container, args.seed)
        finally:
            await container.db.dispose()

    for login in asyncio.run(run()):
        print(f"ready: {login}")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    from pathlib import Path

    from suwapath.seed.generator import generate

    world = generate(args.seed)
    out = Path(args.out or f"storage/seed/world-{args.seed}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(world.model_dump_json(indent=1) + "\n")
    print(
        f"Wrote {out}: {len(world.patients)} patients, {len(world.practitioners)} doctors, "
        f"{len(world.schedules)} sessions, {len(world.users)} accounts"
    )
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

    agents = sub.add_parser("agents", help="run agent jobs (patient companion, guardian, doctor)")
    agents.set_defaults(handler=_cmd_agents)

    clock = sub.add_parser("clock", help="show or advance the simulated clock (development, demo)")
    clock.add_argument("action", choices=["show", "advance"])
    clock.add_argument("duration", nargs="?", type=_parse_duration, default=None)
    clock.set_defaults(handler=_cmd_clock)

    seed = sub.add_parser("seed", help="synthetic world (spec 16); loading arrives with issue I01")
    seed_sub = seed.add_subparsers(dest="seed_command", required=True)
    generate_cmd = seed_sub.add_parser("generate", help="write the deterministic world as JSON")
    generate_cmd.add_argument("--seed", type=int, default=42)
    generate_cmd.add_argument("--out", default=None)
    generate_cmd.set_defaults(handler=_cmd_seed)
    accounts_cmd = seed_sub.add_parser(
        "accounts", help="create the five demo sign-ins with DEMO_PASSWORD (not in production)"
    )
    accounts_cmd.add_argument("--seed", type=int, default=42)
    accounts_cmd.set_defaults(handler=_cmd_seed_accounts)

    openapi = sub.add_parser("openapi", help="write the OpenAPI document for the frontend (API-3)")
    openapi.add_argument("--out", default="frontend/src/shared/api/openapi.json")
    openapi.set_defaults(handler=_cmd_openapi)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
