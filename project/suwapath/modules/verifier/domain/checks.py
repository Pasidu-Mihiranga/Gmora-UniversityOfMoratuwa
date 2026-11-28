"""Stage 1 of the verifier (VER-2): six deterministic checks, all run on every message.

`scope` and `canary` failures cannot be fixed by the author (the message is blocked); the others
can (the message is returned for revision). The checks see only the message, the recipient's
access decision and the referenced sources, never the author's prompt (VER-1).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterator
from typing import Any

from pydantic import ValidationError

from suwapath.contracts.agents import INTENTS, AgentMessage
from suwapath.contracts.enums import DataCategory, Language, Urgency
from suwapath.contracts.ports.safety import WordingHit
from suwapath.contracts.ports.verifier import CheckResult, VerificationContext

WordingCheckFn = Callable[[str, Language], list[WordingHit]]
TEXT_KEYS = frozenset({"text", "summary", "note", "message"})
MAX_TEXT = 4000

# Open fact keys a message may carry, and the category each one reveals (guardian alerts).
FACT_CATEGORY: dict[str, DataCategory | None] = {
    "missed_count": DataCategory.ADHERENCE,
    "slots_missed": DataCategory.ADHERENCE,
    "missed_doses": DataCategory.ADHERENCE,
    "adherence_rate": DataCategory.ADHERENCE,
    "patient_ok": DataCategory.ADHERENCE,  # the answer to an adherence check-in
    "medication_names": DataCategory.MEDICATIONS,
    "next_appointment": DataCategory.APPOINTMENTS,
    "urgency": None,  # navigation metadata, no record data
}


def texts(body: Any) -> Iterator[str]:
    """The free text in a body: string values under text-like keys, at any depth."""
    if isinstance(body, dict):
        for key, value in body.items():
            if key in TEXT_KEYS and isinstance(value, str):
                yield value
            else:
                yield from texts(value)
    elif isinstance(body, list):
        for item in body:
            yield from texts(item)


def _language(message: AgentMessage) -> Language:
    value = message.body.get("language")
    return Language(value) if value in {lang.value for lang in Language} else Language.EN


class ScopeCheck:
    """The recipient may see every category in `consent_scope`, and the content stays inside it."""

    name = "scope"

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult:
        codes: list[str] = []
        access = context.recipient_access
        if access is None:
            codes.append("no_access_decision")
        elif not access.allowed:
            codes.append(access.reason or "recipient_not_allowed")
        facts = message.body.get("facts")
        if isinstance(facts, dict):
            for key in sorted(facts):
                if key not in FACT_CATEGORY:
                    codes.append(f"unknown_fact:{key}"[:40])
                    continue
                category = FACT_CATEGORY[key]
                if category is not None and category not in message.consent_scope:
                    codes.append(f"fact_out_of_scope:{key}"[:40])
        return CheckResult(check=self.name, passed=not codes, fixable=False, codes=tuple(codes))


class WordingCheck:
    """No diagnosis, reassurance, dosing, probability or banned term (SAF-20), si and en."""

    name = "wording"

    def __init__(self, wording: WordingCheckFn) -> None:
        self._wording = wording

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult:
        language = _language(message)
        hits = sorted(
            {h.pattern_id for text in texts(message.body) for h in self._wording(text, language)}
        )
        return CheckResult(check=self.name, passed=not hits, codes=tuple(hits))


# Phrases that tell a person it is fine to wait (checked when the floor is urgent or higher).
_WAIT = re.compile(
    "|".join(
        (
            r"\bno (?:need to )?(?:hurry|rush)\b",
            r"\b(?:it|this) can wait\b",
            r"\bcan wait\b",
            r"\bwait (?:a few|some|two|three|\d+) (?:days|weeks)\b",
            r"\bnot urgent\b",
            r"\bnext week is (?:fine|ok|okay)\b",
            r"\bwhenever (?:you are|you're) free\b",
            r"\bno need to go today\b",
            "ඉක්මන් වෙන්න ඕනේ නැහැ",
            "ඉක්මන් වෙන්න ඕනේ නෑ",
            "හදිසි නැහැ",
            "හදිසි නෑ",
            "හදිසියක් නැහැ",
            "පස්සේ බැලුවත් කමක් නැහැ",
            "ඉවසලා බලන්න",
        )
    )
)


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"\s+", " ", text.translate(dict.fromkeys(map(ord, "‌‍"), None)))


class UrgencyCheck:
    """Nothing in the message may imply less urgency than the case floor (SAF-3)."""

    name = "urgency"

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult:
        floor = context.case_urgency_floor
        codes: list[str] = []
        stated = message.body.get("urgency")
        known = {u.value for u in Urgency}
        if floor is not None and stated in known and Urgency(stated).rank < floor.rank:
            codes.append("urgency_below_floor")
        urgent = floor is not None and floor.rank >= Urgency.URGENT_24H.rank
        if urgent and any(_WAIT.search(_clean(text)) for text in texts(message.body)):
            codes.append("implies_waiting")
        return CheckResult(check=self.name, passed=not codes, codes=tuple(codes))


class SourcesCheck:
    """Every sourced line has a source, and every cited source is one we were given (AGT-9)."""

    name = "sources"

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult:
        codes: list[str] = []
        body = message.body
        lines = body.get("lines")
        if isinstance(lines, list):
            for n, line in enumerate(lines):
                ids = line.get("source_ids") if isinstance(line, dict) else None
                if not ids:
                    codes.append(f"line_without_source:{n}")
                elif context.source_texts and any(str(i) not in context.source_texts for i in ids):
                    codes.append(f"unknown_source:{n}")
        answer = body.get("answered") and body.get("value") is not None and "fact_key" in body
        if answer and not body.get("communication_id"):
            codes.append("answer_without_provenance")
        return CheckResult(check=self.name, passed=not codes, codes=tuple(codes))


class CanaryCheck:
    """No value from a field the recipient may not see, by exact match (VER-2, H4)."""

    name = "canary"

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult:
        content = _clean(
            json.dumps(message.body, ensure_ascii=False)
            + " "
            + " ".join(q.value for q in message.quoted)
        )
        leaked = [v for v in context.out_of_scope_values if v.strip() and _clean(v) in content]
        return CheckResult(
            check=self.name,
            passed=not leaked,
            fixable=False,
            codes=("out_of_scope_value",) if leaked else (),
        )


_SINHALA = re.compile("[඀-෿]")
_LATIN = re.compile("[A-Za-z]")


class FormatCheck:
    """The body validates for its intent, text is not too long, and text is in its language."""

    name = "format"

    def run(self, message: AgentMessage, context: VerificationContext) -> CheckResult:
        codes: list[str] = []
        try:
            INTENTS[message.intent].body.model_validate(message.body)
        except ValidationError:
            codes.append("body_invalid")
        declared = message.body.get("language")
        for text in texts(message.body):
            if len(text) > MAX_TEXT:
                codes.append("text_too_long")
            sinhala, latin = len(_SINHALA.findall(text)), len(_LATIN.findall(text))
            if declared == Language.SI.value and latin and not sinhala:
                codes.append("wrong_language")
            if declared == Language.EN.value and sinhala > latin:
                codes.append("wrong_language")
        return CheckResult(check=self.name, passed=not codes, codes=tuple(dict.fromkeys(codes)))


def default_checks(wording: WordingCheckFn) -> list[Any]:
    return [
        ScopeCheck(),
        WordingCheck(wording),
        UrgencyCheck(),
        SourcesCheck(),
        CanaryCheck(),
        FormatCheck(),
    ]
