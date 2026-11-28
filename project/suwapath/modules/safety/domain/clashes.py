"""The clash list (SAF-30 to SAF-32): deterministic checks of a patient's active medicines against
each other, their allergies (by substance and by class) and their conditions, on the demo
formulary. Only active medicines and conditions count."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

import yaml

from suwapath.contracts.fhir.base import CodeableConcept
from suwapath.contracts.fhir.resources import AllergyIntolerance, Condition, MedicationStatement
from suwapath.contracts.ports.safety import Clash

Severity = Literal["contraindicated", "major", "moderate"]
RANK: dict[str, int] = {"moderate": 1, "major": 2, "contraindicated": 3}
ACTIVE_MEDICINE = ("active", "on-hold")
ACTIVE_CONDITION = ("active", "recurrence")


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _codes(concept: CodeableConcept) -> list[str]:
    found = [_key(c.code) for c in concept.coding if c.code]
    if concept.text:
        found.append(_key(concept.text))
    return found


@dataclass(frozen=True)
class Medicine:
    resource_id: str
    name: str | None  # formulary name, None when unknown
    classes: frozenset[str]

    def labels(self) -> frozenset[str]:
        return self.classes | ({self.name} if self.name else frozenset())


@dataclass(frozen=True)
class Formulary:
    version: str
    medicines: dict[str, frozenset[str]]
    aliases: dict[str, str]
    allergy_classes: dict[str, dict[str, Any]]
    interactions: tuple[dict[str, str], ...]
    duplicate_classes: dict[str, str]
    drug_conditions: tuple[dict[str, str], ...]

    @classmethod
    def load(cls, path: Path) -> Formulary:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            version=str(data["version"]),
            medicines={k: frozenset(v) for k, v in data["medicines"].items()},
            aliases=dict(data.get("aliases") or {}),
            allergy_classes=dict(data["allergy_classes"]),
            interactions=tuple(data["interactions"]),
            duplicate_classes=dict(data["duplicate_classes"]),
            drug_conditions=tuple(data["drug_conditions"]),
        )

    def medicine(self, statement: MedicationStatement) -> Medicine:
        codes = _codes(statement.medication)
        name = next(
            (
                self.aliases.get(code, code)
                for code in codes
                if self.aliases.get(code, code) in self.medicines
            ),
            None,
        )
        classes = set(self.medicines.get(name, frozenset())) if name else set()
        classes |= {_key(c.code) for c in statement.medication.coding if c.system == "class"}
        return Medicine(str(statement.id), name, frozenset(classes))

    def check(
        self,
        medications: list[MedicationStatement],
        allergies: list[AllergyIntolerance],
        conditions: list[Condition],
    ) -> list[Clash]:
        meds = [self.medicine(m) for m in medications if m.status in ACTIVE_MEDICINE]
        found: dict[tuple[str, tuple[str, ...]], Clash] = {}

        def add(kind: Any, severity: str, between: tuple[str, ...], reference: str) -> None:
            key = (kind, tuple(sorted(between)))
            current = found.get(key)
            if current is None or RANK[severity] > RANK[current.severity]:
                found[key] = Clash(
                    kind=kind, severity=severity, between=key[1], reference=reference
                )

        for one, two in combinations(meds, 2):
            if one.name is not None and one.name == two.name:
                continue  # the same medicine twice is a record question, not a clash
            for rule in self.interactions:
                a, b = rule["a"], rule["b"]
                if (a in one.labels() and b in two.labels()) or (
                    b in one.labels() and a in two.labels()
                ):
                    add(
                        "drug_drug",
                        rule["severity"],
                        (one.resource_id, two.resource_id),
                        rule["reference"],
                    )
            for klass in sorted(one.classes & two.classes):
                if klass in self.duplicate_classes:
                    add(
                        "duplicate_therapy",
                        "moderate",
                        (one.resource_id, two.resource_id),
                        self.duplicate_classes[klass],
                    )
        for allergy in allergies:
            codes = set(_codes(allergy.code))
            for med in meds:
                if med.name is not None and self.aliases.get(med.name, med.name) in codes:
                    add(
                        "drug_allergy",
                        "contraindicated",
                        (str(allergy.id), med.resource_id),
                        "Recorded allergy to this medicine",
                    )
                for code in sorted(codes):
                    entry = self.allergy_classes.get(code)
                    if entry is None:
                        continue
                    if med.classes & set(entry["matches"]):
                        add(
                            "drug_allergy",
                            "contraindicated",
                            (str(allergy.id), med.resource_id),
                            entry["reference"],
                        )
                    for cross in entry.get("cross") or ():
                        if cross["class"] in med.classes:
                            add(
                                "drug_allergy",
                                cross["severity"],
                                (str(allergy.id), med.resource_id),
                                entry["reference"],
                            )
        active = [c for c in conditions if c.clinical_status in ACTIVE_CONDITION]
        for condition in active:
            condition_codes = _codes(condition.code)
            for rule in self.drug_conditions:
                if not any(code.startswith(rule["condition"]) for code in condition_codes):
                    continue
                for med in meds:
                    if rule["drug"] in med.labels():
                        add(
                            "drug_condition",
                            rule["severity"],
                            (med.resource_id, str(condition.id)),
                            rule["reference"],
                        )
        return sorted(found.values(), key=lambda c: (-RANK[c.severity], c.kind, c.between))
