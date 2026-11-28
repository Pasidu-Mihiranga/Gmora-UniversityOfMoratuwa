"""`agents` module registration (ARC-3). Binds the agent registry, delivery and the supervisor.

The runtime's dependencies are other modules' ports. They are resolved when an event or job needs
them, because those modules register in any order; a missing port fails loudly and the event stays
retryable (it is never dropped).
"""

from __future__ import annotations

from suwapath.modules.agents.api.router import router
from suwapath.modules.agents.application.context import Dependencies
from suwapath.modules.agents.application.guardian import GuardianAgent
from suwapath.modules.agents.application.runtime import (
    AgentRegistry,
    Delivery,
    register_supervisor,
)
from suwapath.modules.agents.application.skeleton_agents import CompanionAgent
from suwapath.platform.registry import ModuleContext

REQUIRED_PORTS = (
    "cases",
    "access_policy",
    "verifier",
    "notifications",
    "agent_directory",
    "care",
)


class DependencyMissing(RuntimeError):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"agent runtime needs ports that are not bound: {', '.join(missing)}")
        self.missing = missing


def register(container: ModuleContext) -> None:
    registry = AgentRegistry([GuardianAgent(), CompanionAgent()])

    def dependencies() -> Dependencies:
        ports = container.ports
        missing = [name for name in REQUIRED_PORTS if name not in ports]
        if missing:
            raise DependencyMissing(missing)
        return Dependencies(
            cases=ports["cases"],
            policy=ports["access_policy"],
            verifier=ports["verifier"],
            notifications=ports["notifications"],
            directory=ports["agent_directory"],
            missed_doses=None,
            latest_patient_text=ports["cases"].latest_patient_text,
            adherence=ports["care"].adherence,
        )

    delivery = Delivery(dependencies)
    register_supervisor(container.subscribers, dependencies, delivery, registry.kinds())
    container.ports["agent_registry"] = registry
    container.ports["agent_dependencies"] = dependencies
    container.routers.append(router)
