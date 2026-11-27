"""Reading a patient's answer to an intake question, with no model (KNW-1 tier T0).

Sinhala, Sinhala in Latin script and English. "Not sure" is its own answer: the navigation engine
treats it as "yes" on red-flag screens (AGT-38). Anything unclear returns None, and the companion
asks a decision model or the patient again; it never guesses.
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

AnswerType = Literal["yes_no", "choice", "number", "duration"]

UNSURE = (
    "not sure",
    "dont know",
    "don't know",
    "do not know",
    "maybe",
    "unsure",
    "දන්නේ නැහැ",
    "දන්නෙ නෑ",
    "විශ්වාස නැහැ",
    "සමහර විට",
    "danne naha",
    "danne na",
)
NO = ("no", "nope", "not really", "naha", "nehe", "na", "නැහැ", "නෑ", "නැත")
YES = ("yes", "yeah", "yep", "y", "ow", "owu", "ඔව්", "ඔව්", "ඔවු")
_WORD = re.compile("[\\w\u0d80-\u0dff\u200d']+")  # words, Sinhala block, ZWJ
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class Parsed(NamedTuple):
    label: str  # a stable code or normalized text, for the case fact
    value: str | float  # what goes to the navigation engine


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _has(text: str, tokens: list[str], phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        if " " in phrase or not phrase.isascii():
            if phrase in text:
                return True
        elif phrase in tokens:
            return True
    return False


def parse_answer(
    text: str, answer_type: AnswerType, choices: tuple[str, ...] = ()
) -> Parsed | None:
    lowered = text.casefold().strip()
    tokens = _tokens(lowered)
    if not tokens:
        return None
    if _has(lowered, tokens, UNSURE):
        return Parsed("unsure", "unsure")
    if answer_type == "yes_no":
        yes, no = _has(lowered, tokens, YES), _has(lowered, tokens, NO)
        if yes == no:
            return None
        return Parsed("yes", "yes") if yes else Parsed("no", "no")
    if answer_type == "choice":
        found = [c for c in choices if c.casefold() in lowered]
        return Parsed(found[0], found[0]) if len(found) == 1 else None
    number = _NUMBER.search(lowered)
    if number is None:
        return None
    if answer_type == "number":
        return Parsed(number.group(), float(number.group()))
    days = f"{number.group()} days"
    return Parsed(days, days)
