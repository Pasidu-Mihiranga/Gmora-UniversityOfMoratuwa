"""agents: the runtime other modules use to start agent work and deliver messages."""

from __future__ import annotations

from typing import Protocol

from suwapath.contracts.agents import AgentJob, AgentMessage
from suwapath.contracts.ids import JobId


class AgentRuntime(Protocol):
    async def enqueue(self, job: AgentJob) -> JobId:
        """Writes an agents_jobs row inside the caller's unit of work (EVT-10)."""
        ...

    async def submit_message(self, message: AgentMessage) -> None:
        """Runs the verifier for messages that need it, then delivers or returns (AGT-10)."""
        ...
