"""UUIDv7 generation (CON-1): time-ordered IDs for index locality."""

from __future__ import annotations

import os
import time
from uuid import UUID


def new_id() -> UUID:
    """RFC 9562 UUIDv7: 48-bit Unix milliseconds, version 7, variant 10, 74 random bits."""
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = rand >> 68  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits
    value = (unix_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return UUID(int=value)


def new_trace_id() -> str:
    """W3C trace id: 32 lowercase hex characters, not all zero."""
    return os.urandom(16).hex()
