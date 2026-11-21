"""Suggested replies and actions shown as chips (AGT-36 to AGT-39, FE-50 to FE-52)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import Language

MAX_SUGGESTIONS = 3
MAX_LABEL_CHARS = 40


class Suggestion(Contract):
    kind: Literal["reply", "action"]
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    language: Language
    source: Literal["step", "model"]
    # reply: text placed in the input bar (equals the label; the user may edit it before sending)
    fill_text: str | None = None
    # action: a server-built candidate the client opens as a confirmation step, never a direct write
    action_id: str | None = None
    action_kind: (
        Literal[
            "book_slot",
            "show_doctors",
            "call_1990",
            "call_guardian",
            "dose_taken",
            "remind_later",
            "ask_fact",
            "approve_reply",
            "open_record",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _shape(self) -> Suggestion:
        if self.kind == "reply":
            if self.fill_text != self.label:
                raise ValueError("a reply chip must fill exactly its label (FE-52)")
            if self.action_id or self.action_kind:
                raise ValueError("a reply chip carries no action")
        else:
            if not (self.action_id and self.action_kind):
                raise ValueError("an action chip needs a server-built action (AGT-37)")
            if self.source == "model":
                raise ValueError("action chips are built by the server, not the model (AGT-37)")
        return self


class Suggestions(Contract):
    items: tuple[Suggestion, ...] = Field(default=(), max_length=MAX_SUGGESTIONS)
