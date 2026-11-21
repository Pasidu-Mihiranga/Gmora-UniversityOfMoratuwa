"""Base model and small shared value types."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    """Base for every contract model: immutable and strict about unknown fields (CON-3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class UntrustedText(Contract):
    """Text a person typed or a document contained (CON-4).

    Always rendered to models inside a quoted data block, never as instructions (GW-21).
    """

    value: str = Field(max_length=20_000)
    source: str = Field(description="e.g. patient_message, report_text, guardian_message")


class Page[T](Contract):
    """Cursor-paginated list (API-5)."""

    items: list[T]
    next_cursor: str | None = None
