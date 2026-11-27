"""The agent worker loop (`python main.py agents`): claims jobs and runs them until stopped."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from suwapath.modules.agents.application.runtime import AgentRunner

logger = logging.getLogger("suwapath.agents")


class AgentWorker:
    def __init__(self, runner: AgentRunner, worker_id: str, idle_seconds: float = 1.0) -> None:
        self.runner = runner
        self.worker_id = worker_id
        self.idle_seconds = idle_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("agent worker started", extra={"worker_id": self.worker_id})
        while not self._stop.is_set():
            try:
                busy = await self.runner.run_once(self.worker_id)
            except Exception as exc:  # a claim failure must not stop the worker
                logger.warning("agent worker error", extra={"error_code": type(exc).__name__})
                busy = False
            if not busy:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.idle_seconds)
        logger.info("agent worker stopped", extra={"worker_id": self.worker_id})
