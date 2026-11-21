"""Per-process token buckets by route class (SG-20). Quotas bound model use across processes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

RouteClass = Literal["auth", "chat", "search", "upload", "default"]

PER_MINUTE: dict[RouteClass, int] = {
    "auth": 10,
    "chat": 20,
    "search": 60,
    "upload": 10,
    "default": 120,
}


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, per_minute: dict[RouteClass, int] | None = None) -> None:
        self.per_minute = per_minute or PER_MINUTE
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def allow(self, route_class: RouteClass, key: str, now: float | None = None) -> bool:
        capacity = self.per_minute[route_class]
        rate = capacity / 60.0
        current = time.monotonic() if now is None else now
        bucket = self._buckets.get((route_class, key))
        if bucket is None:
            bucket = self._buckets[(route_class, key)] = _Bucket(tokens=capacity, updated=current)
        bucket.tokens = min(capacity, bucket.tokens + (current - bucket.updated) * rate)
        bucket.updated = current
        if bucket.tokens < 1:
            return False
        bucket.tokens -= 1
        return True


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(limiter: RateLimiter, route_class: RouteClass, key: str) -> None:
    if not limiter.allow(route_class, key):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")
