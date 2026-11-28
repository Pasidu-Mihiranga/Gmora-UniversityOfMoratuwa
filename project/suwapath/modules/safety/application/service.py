"""The safety port (SAF-1 to SAF-32): deterministic, no model and no I/O after loading. The
content files are read once when the module registers."""

from __future__ import annotations

from pathlib import Path

from suwapath.contracts.common import UntrustedText
from suwapath.contracts.enums import Language, Urgency, max_urgency
from suwapath.contracts.fhir.resources import AllergyIntolerance, Condition, MedicationStatement
from suwapath.contracts.ports.safety import (
    Clash,
    PatientContext,
    RedFlagMatch,
    SafetyAssessment,
    WordingHit,
)
from suwapath.modules.safety.domain.clashes import Formulary
from suwapath.modules.safety.domain.lexicon import Lexicon
from suwapath.modules.safety.domain.rules import RULES, RULES_VERSION, Rule
from suwapath.modules.safety.domain.wording import WordingRules

CONTENT = Path(__file__).resolve().parent.parent / "content"


class RuleSafetyService:
    def __init__(
        self,
        lexicon: Lexicon,
        wording: WordingRules,
        formulary: Formulary,
        rules: tuple[Rule, ...] = RULES,
    ) -> None:
        self.lexicon, self.wording, self.formulary, self.rules = lexicon, wording, formulary, rules

    @classmethod
    def from_content(cls, folder: Path = CONTENT) -> RuleSafetyService:
        return cls(
            Lexicon.load(folder / "lexicon.yaml"),
            WordingRules.load(folder),
            Formulary.load(folder / "formulary.yaml"),
        )

    def assess(
        self, text: UntrustedText, context: PatientContext, language: Language | None = None
    ) -> SafetyAssessment:
        # Every script is always read: people mix Sinhala, English and Sinhala in Latin letters.
        concepts = self.lexicon.concepts(text.value)
        matches: list[RedFlagMatch] = []
        help_lines: list[str] = []
        for rule in self.rules:
            fired = rule.fires(concepts, context)
            if fired is None:
                continue
            matches.append(
                RedFlagMatch(
                    rule_id=rule.rule_id,
                    label_key=f"red_flag.{rule.rule_id}",
                    urgency=rule.urgency,
                    matched_concepts=fired,
                )
            )
            help_lines.extend(line for line in rule.help_lines if line not in help_lines)
        floor = max_urgency(*(m.urgency for m in matches))  # SAF-3
        emergency = floor is Urgency.EMERGENCY
        return SafetyAssessment(
            concepts=concepts,
            red_flags=tuple(matches),
            urgency_floor=floor,
            emergency=emergency,
            help_lines=tuple(help_lines) if emergency else (),
            rules_version=f"{RULES_VERSION}+{self.lexicon.version}",
        )

    def check_wording(self, text: str, language: Language) -> list[WordingHit]:
        return self.wording.check(text, language)

    def check_clashes(
        self,
        medications: list[MedicationStatement],
        allergies: list[AllergyIntolerance],
        conditions: list[Condition],
    ) -> list[Clash]:
        return self.formulary.check(medications, allergies, conditions)
