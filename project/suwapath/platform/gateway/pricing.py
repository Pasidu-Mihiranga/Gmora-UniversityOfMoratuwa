"""Model prices per million tokens, used to estimate cost before a call (GW-10, CUS-9b).

These are planning estimates, marked as such in every record (`cost_is_estimate=True`); update them
from the provider's pricing page. Jev returns its real cost in `usage.cost`, which is used instead.
"""

from __future__ import annotations

# model id -> (input USD per 1M tokens, output USD per 1M tokens)
ESTIMATED_PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
}
DEFAULT_PRICE = (0.50, 3.00)  # deliberately pessimistic for unknown models


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = ESTIMATED_PRICES.get(model, DEFAULT_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def estimate_tokens(text: str) -> int:
    """Rough token estimate (Sinhala script runs longer per character than English)."""
    return max(1, len(text) // 3)
