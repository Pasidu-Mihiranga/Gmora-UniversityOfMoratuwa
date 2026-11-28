"""Banned wording in patient-facing text (SAF-20, SAF-21): data-driven patterns per language."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from suwapath.contracts.enums import Language
from suwapath.contracts.ports.safety import WordingHit

Category = Literal["diagnosis", "reassurance", "dosing", "probability", "banned_term"]
_JOINERS = dict.fromkeys(map(ord, "‌‍"), None)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold().translate(_JOINERS)
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


@dataclass(frozen=True)
class BannedPattern:
    pattern_id: str
    category: Category
    regex: re.Pattern[str]
    example: str
    near_miss: str


@dataclass(frozen=True)
class WordingRules:
    patterns: dict[Language, tuple[BannedPattern, ...]]

    @classmethod
    def load(cls, folder: Path) -> WordingRules:
        patterns: dict[Language, tuple[BannedPattern, ...]] = {}
        for language in Language:
            data: dict[str, Any] = yaml.safe_load(
                (folder / f"banned.{language.value}.yaml").read_text(encoding="utf-8")
            )
            patterns[language] = tuple(
                BannedPattern(
                    pattern_id=p["id"],
                    category=p["category"],
                    regex=re.compile(normalize(p["regex"])),
                    example=p["example"],
                    near_miss=p["near_miss"],
                )
                for p in data["patterns"]
            )
        return cls(patterns)

    def check(self, text: str, language: Language) -> list[WordingHit]:
        clean = normalize(text)
        # Both lists run: a Sinhala reply may carry English words and the other way round.
        order = [language, *(other for other in Language if other is not language)]
        return [
            WordingHit(pattern_id=p.pattern_id, category=p.category, language=lang)
            for lang in order
            for p in self.patterns[lang]
            if p.regex.search(clean)
        ]
