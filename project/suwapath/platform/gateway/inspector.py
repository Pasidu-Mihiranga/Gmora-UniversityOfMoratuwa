"""Outbound PHI inspector (SEC-30 step 4). Runs on every payload before it leaves for a provider.

Detects Sri Lankan NIC numbers (old: 9 digits + V/X; new: 12 digits), Sri Lankan phone numbers,
email addresses, and any known identifier value passed by the caller. A hit blocks the call.
Pseudonymization of known identifiers (steps 1 to 3) is added by the PHI minimization module (M13).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from suwapath.contracts.errors import PhiEgressBlocked

_PATTERNS: dict[str, re.Pattern[str]] = {
    "nic_old": re.compile(r"(?<!\d)\d{9}[VvXx](?![A-Za-z0-9])"),
    "nic_new": re.compile(r"(?<!\d)(?:19|20)\d{10}(?!\d)"),
    "phone_lk": re.compile(r"(?<!\d)(?:\+94|0094|0)\s?7\d(?:[\s-]?\d){7}(?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}


def find_identifiers(text: str, known_values: Iterable[str] = ()) -> list[str]:
    """Names of the detectors that matched (never the matched values)."""
    hits = [name for name, pattern in _PATTERNS.items() if pattern.search(text)]
    lowered = text.casefold()
    if any(v and len(v) >= 3 and v.casefold() in lowered for v in known_values):
        hits.append("known_identifier")
    return hits


def assert_no_phi(texts: Iterable[str], known_values: Iterable[str] = ()) -> None:
    known = list(known_values)
    for text in texts:
        hits = find_identifiers(text, known)
        if hits:
            raise PhiEgressBlocked("outbound payload contains identifiers", detectors=hits)
