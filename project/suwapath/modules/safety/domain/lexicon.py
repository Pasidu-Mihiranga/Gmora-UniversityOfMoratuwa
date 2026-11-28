"""Concept extraction (SAF-2): whole-word phrase matching in English, Sinhala and Sinhala written in
Latin script. Deterministic and fast: one compiled pattern per phrase, built once."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCRIPTS = ("en", "si", "si_latn")
# A word character for boundaries: Latin letters, digits and the whole Sinhala block (letters and
# vowel signs), so "una" does not match inside "unable" and "උණ" not inside "උණුසුම්".
_WORD = r"0-9a-z඀-෿"
_JOINERS = dict.fromkeys(map(ord, "‌‍"), None)


def normalize(text: str) -> str:
    """Lower case, NFC, no zero-width joiners, and every run of non-word characters one space."""
    text = unicodedata.normalize("NFC", text).casefold().translate(_JOINERS)
    text = text.replace("’", "'")
    return " " + re.sub(rf"[^{_WORD}']+", " ", text).strip() + " "


STEM_SCRIPTS = ("si", "si_latn")  # words take endings: the last word matches as a stem
MIN_STEM = 4  # shorter words (උණ, una, na) stay whole words


def _compile(phrase: str, script: str) -> re.Pattern[str]:
    words = normalize(phrase.rstrip("*")).split()
    stem = phrase.endswith("*") or (script in STEM_SCRIPTS and len(words[-1]) >= MIN_STEM)
    body = r"\s+".join(re.escape(w) for w in words)
    tail = rf"[{_WORD}]*" if stem else ""
    return re.compile(rf"(?<![{_WORD}]){body}{tail}(?![{_WORD}])")


@dataclass(frozen=True)
class Lexicon:
    version: str
    phrases: dict[str, dict[str, tuple[str, ...]]]  # concept -> script -> phrases
    _patterns: tuple[tuple[str, re.Pattern[str]], ...]

    @classmethod
    def load(cls, path: Path) -> Lexicon:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        phrases = {
            concept: {script: tuple(entry.get(script) or ()) for script in SCRIPTS}
            for concept, entry in data["concepts"].items()
        }
        patterns = tuple(
            (concept, _compile(phrase, script))
            for concept, by_script in phrases.items()
            for script in SCRIPTS
            for phrase in by_script[script]
        )
        return cls(version=str(data["version"]), phrases=phrases, _patterns=patterns)

    def concepts(self, text: str) -> frozenset[str]:
        clean = normalize(text)
        return frozenset(concept for concept, pattern in self._patterns if pattern.search(clean))
