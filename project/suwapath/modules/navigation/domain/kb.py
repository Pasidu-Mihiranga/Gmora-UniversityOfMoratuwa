"""The navigation knowledge base (NAV-4): versioned files in `kb/`, loaded once at startup.

Concept words are matched like the safety lexicon: whole words, case-insensitive, and in Sinhala
scripts the last word of a phrase (4 letters or more) also matches with any ending.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from suwapath.contracts.enums import Specialty, Urgency

KB = Path(__file__).resolve().parent.parent / "kb"
SCRIPTS = ("en", "si", "si_latn")
_WORD = r"0-9a-z඀-෿"
_JOINERS = dict.fromkeys(map(ord, "‌‍"), None)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold().translate(_JOINERS)
    return " " + re.sub(rf"[^{_WORD}']+", " ", text.replace("’", "'")).strip() + " "


def _compile(phrase: str, script: str) -> re.Pattern[str]:
    words = normalize(phrase.rstrip("*")).split()
    stem = phrase.endswith("*") or (script != "en" and len(words[-1]) >= 4)
    body = r"\s+".join(re.escape(w) for w in words)
    return re.compile(rf"(?<![{_WORD}]){body}{'[' + _WORD + ']*' if stem else ''}(?![{_WORD}])")


@dataclass(frozen=True)
class Concept:
    id: str
    safety: frozenset[str] = frozenset()
    chronic: tuple[str, ...] = ()
    context: bool = False
    askable: bool = True
    screen: str | None = None
    ambiguous: str | None = None


@dataclass(frozen=True)
class Group:
    id: str
    likelihood: dict[str, float]
    specialties: tuple[Specialty, ...]
    urgency: Urgency


@dataclass(frozen=True)
class Complaint:
    id: str
    concepts: frozenset[str]
    specialties: tuple[Specialty, ...]
    urgency: Urgency


@dataclass(frozen=True)
class Question:
    concept: str
    text: dict[str, str]  # language -> text
    answer_type: str = "yes_no"
    choices: dict[str, dict[str, Any]] = field(default_factory=dict)  # clarify questions only


@dataclass(frozen=True)
class KnowledgeBase:
    version: str
    concepts: dict[str, Concept]
    groups: tuple[Group, ...]
    priors: dict[str, dict[str, dict[str, float]]]  # age band -> sex -> group -> prior
    complaints: tuple[Complaint, ...]
    questions: dict[str, Question]
    clarify: dict[str, Question]  # ambiguity name -> clarifying question
    red_flag_screen: frozenset[str]
    screens: dict[str, tuple[str, ...]]
    child_under: int
    child_specialty: Specialty
    _words: tuple[tuple[str, re.Pattern[str]], ...]

    @property
    def scored(self) -> frozenset[str]:
        return frozenset(self.groups[0].likelihood) if self.groups else frozenset()

    def concepts_in(self, text: str) -> frozenset[str]:
        clean = normalize(text)
        return frozenset(c for c, pattern in self._words if pattern.search(clean))

    def from_safety(self, safety_concepts: frozenset[str]) -> frozenset[str]:
        return frozenset(c.id for c in self.concepts.values() if c.safety & safety_concepts)

    @classmethod
    def load(cls, folder: Path = KB) -> KnowledgeBase:
        def read(name: str) -> dict[str, Any]:
            data: dict[str, Any] = yaml.safe_load((folder / name).read_text(encoding="utf-8"))
            return data

        concepts_file, groups_file = read("concepts.yaml"), read("condition_groups.yaml")
        routing, complaints_file = read("routing.yaml"), read("complaints.yaml")
        questions_file = read("questions.yaml")
        concepts: dict[str, Concept] = {}
        words: list[tuple[str, re.Pattern[str]]] = []
        for cid, entry in concepts_file["concepts"].items():
            concepts[cid] = Concept(
                id=cid,
                safety=frozenset(entry.get("safety") or ()),
                chronic=tuple(entry.get("chronic") or ()),
                context=bool(entry.get("context")),
                askable=entry.get("askable", True),
                screen=entry.get("screen"),
                ambiguous=entry.get("ambiguous"),
            )
            for script in SCRIPTS:
                for phrase in (entry.get("words") or {}).get(script) or ():
                    words.append((cid, _compile(phrase, script)))
        routes = routing["groups"]
        groups = tuple(
            Group(
                id=g["id"],
                likelihood={k: float(v) for k, v in g["likelihood"].items()},
                specialties=tuple(Specialty(s) for s in routes[g["id"]]["specialties"]),
                urgency=Urgency(routes[g["id"]]["urgency"]),
            )
            for g in groups_file["groups"]
        )
        complaints = tuple(
            Complaint(
                id=cid,
                concepts=frozenset(c["concepts"]),
                specialties=tuple(Specialty(s) for s in c["specialties"]),
                urgency=Urgency(c["urgency"]),
            )
            for cid, c in complaints_file["complaints"].items()
        )
        questions = {
            cid: Question(concept=cid, text={"en": q["en"], "si": q["si"]})
            for cid, q in questions_file["questions"].items()
        }
        clarify = {
            name: Question(
                concept=q["for"],
                text={"en": q["en"], "si": q["si"]},
                answer_type="choice",
                choices=dict(q["choices"]),
            )
            for name, q in questions_file["clarify"].items()
        }
        version = "+".join(
            str(f["version"])
            for f in (concepts_file, groups_file, routing, complaints_file, questions_file)
        )
        kb = cls(
            version=version,
            concepts=concepts,
            groups=groups,
            priors=groups_file["priors"],
            complaints=complaints,
            questions=questions,
            clarify=clarify,
            red_flag_screen=frozenset(concepts_file.get("red_flag_screen") or ()),
            screens={k: tuple(v) for k, v in (concepts_file.get("screens") or {}).items()},
            child_under=int(routing["child_under"]),
            child_specialty=Specialty(routing["child_specialty"]),
            _words=tuple(words),
        )
        kb.check()
        return kb

    def check(self) -> None:
        """Refuse to start with a broken knowledge base (a typo must never silently disable a
        question or a route)."""
        known = set(self.concepts)
        for group in self.groups:
            missing = set(group.likelihood) - known
            if missing:
                raise ValueError(f"group {group.id}: unknown concepts {sorted(missing)}")
        for complaint in self.complaints:
            if complaint.concepts - known:
                raise ValueError(f"complaint {complaint.id}: unknown concepts")
        for name in (*self.questions, *self.red_flag_screen, *self.screens):
            if name not in known:
                raise ValueError(f"unknown concept {name}")
        for follow_ups in self.screens.values():
            if set(follow_ups) - set(self.questions):
                raise ValueError("a screen follow-up has no question")
        for concept in self.concepts.values():
            if concept.ambiguous and concept.ambiguous not in self.clarify:
                raise ValueError(f"{concept.id}: no clarifying question {concept.ambiguous}")
