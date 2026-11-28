"""HTTP routes for the `ops` module. Stubs until issue U08 implements them (W0.8)."""

from fastapi import Depends, Request

from suwapath.contracts.enums import Role
from suwapath.contracts.errors import NotFound
from suwapath.platform.http.auth import require
from suwapath.platform.http.stubs import ALL_ROLES, StubRoute, stub_router

ROUTES = [
    StubRoute("GET", "/ops/overview", (Role.SYSTEM_ADMIN,), "AI operations overview", "U08"),
    StubRoute("GET", "/ops/model-calls", (Role.SYSTEM_ADMIN,), "Model call ledger", "U08"),
    StubRoute("GET", "/ops/budgets", (Role.SYSTEM_ADMIN,), "Budgets", "U08"),
    StubRoute("PUT", "/ops/budgets/{scope}", (Role.SYSTEM_ADMIN,), "Set a budget", "U08"),
    StubRoute("GET", "/ops/dead-letters", (Role.SYSTEM_ADMIN,), "Dead letters", "U08"),
    StubRoute(
        "POST", "/ops/dead-letters/{delivery_id}/replay", (Role.SYSTEM_ADMIN,), "Replay", "U08"
    ),
    StubRoute("GET", "/ops/flags", (Role.SYSTEM_ADMIN,), "Feature flags", "U08"),
    StubRoute("PUT", "/ops/flags/{key}", (Role.SYSTEM_ADMIN,), "Set a flag", "U08"),
]

router = stub_router("ops", ROUTES)

# Mounted only when SUWAPATH_ENV is development, demo or test (EVT-18).
DEV_ROUTES = [
    StubRoute("GET", "/dev/clock", ALL_ROLES, "Simulated clock (dev and demo)", "U08"),
    StubRoute("POST", "/dev/clock/advance", ALL_ROLES, "Advance the clock (dev and demo)", "U08"),
]

dev_router = stub_router("dev", DEV_ROUTES)


@dev_router.post(
    "/dev/scenario/reset",
    summary="Reset the demo world (dev and demo)",
    operation_id="dev_post_dev_scenario_reset",
    dependencies=[Depends(require(*ALL_ROLES))],
)
async def reset(request: Request) -> dict[str, int]:
    """Empty every table and load the synthetic world again (spec 16). Mounted only in
    development, demo and test (EVT-18); every signed-in demo user may use the dev panel."""
    reset_world = getattr(request.app.state, "reset_world", None)
    if reset_world is None:
        raise NotFound()
    counts: dict[str, int] = await reset_world()
    return counts
