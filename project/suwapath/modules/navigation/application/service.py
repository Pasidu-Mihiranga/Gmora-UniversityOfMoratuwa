"""The navigation port (spec 08, NAV-1 to NAV-9): the engine over the case's own facts.

The engine keeps no state. Each step reads the case's facts (what the patient said and answered,
red flags), adds what this step newly read, and returns either the next question or the result,
together with the new findings for the companion to record (NAV-4). Every patient message and
every "yes" to a red-flag concept goes through the safety rules first (NAV-1); an emergency stops
navigation at once and never waits for a question.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from typing import Any

from suwapath.contracts.cases import CaseFact, CaseState
from suwapath.contracts.common import Page, UntrustedText
from suwapath.contracts.enums import Language, Specialty, Urgency, max_urgency
from suwapath.contracts.fhir.base import GeoPoint
from suwapath.contracts.ids import CaseId
from suwapath.contracts.ports.navigation import (
    DoctorOption,
    DoctorQuery,
    Finding,
    Itinerary,
    NavigationQuestion,
    NavigationResult,
    NavigationStep,
    SpecialtyOption,
)
from suwapath.contracts.ports.safety import PatientContext, SafetyAssessment
from suwapath.contracts.principal import Principal
from suwapath.modules.navigation.domain.engine import Ask, Knowns, decide, next_question
from suwapath.modules.navigation.domain.kb import KnowledgeBase
from suwapath.modules.navigation.domain.matching import (
    TRIP_RADIUS_KM,
    bounding_box,
    plan_trips,
    rank_doctors,
)
from suwapath.platform.clock import Clock

UNSURE = "unsure"
MENTIONED = "mentioned"
MAX_DOCTORS = 20
MAX_TRIP_NEEDS = 3  # a finding read from the patient's words, not an answer


class KbNavigationService:
    def __init__(
        self, kb: KnowledgeBase, ports: Callable[[], Mapping[str, Any]], clock: Clock
    ) -> None:
        self.kb = kb
        self._ports = ports
        self._clock = clock

    # ----- the port ------------------------------------------------------------------------------

    async def start(
        self,
        principal: Principal,
        case_id: CaseId,
        text: UntrustedText,
        context: PatientContext,
        language: Language,
    ) -> NavigationStep:
        assessment = self._safety().assess(text, context, language)  # NAV-1: always first
        read = self.kb.concepts_in(text.value) | self.kb.from_safety(assessment.concepts)
        state = await self._case(principal, case_id)
        knowns = self._knowns(state, context, assessment.urgency_floor)
        new = read - knowns.present - knowns.absent
        knowns = replace(knowns, present=knowns.present | new)
        findings = tuple(Finding(concept=c, present=True) for c in sorted(new))
        combined = self._screen(knowns, context)  # findings from earlier turns count too
        if assessment.emergency or combined.emergency:
            return NavigationStep(emergency=True, findings=findings)
        knowns = replace(knowns, floor=max_urgency(knowns.floor, combined.urgency_floor))
        return self._step(knowns, language, assessment.rules_version, findings, state)

    async def answer(
        self,
        principal: Principal,
        case_id: CaseId,
        concept: str,
        answer: str | float | bool,
        context: PatientContext | None = None,
    ) -> NavigationStep:
        state = await self._case(principal, case_id)
        context = context or PatientContext()
        knowns = self._knowns(state, context, None)
        knowns, findings = self._apply(knowns, concept, answer)
        combined = self._screen(knowns, context)  # NAV-1: every answer is re-assessed
        if combined.emergency:
            return NavigationStep(emergency=True, findings=findings)
        knowns = replace(knowns, floor=max_urgency(knowns.floor, combined.urgency_floor))
        rules_version = combined.rules_version
        return self._step(knowns, state.language, rules_version, findings, state)

    async def result(self, principal: Principal, case_id: CaseId) -> NavigationResult | None:
        state = await self._case(principal, case_id)
        knowns = self._knowns(state, PatientContext(), None)
        if not knowns.present and not knowns.absent:
            return None  # navigation has not started on this case
        rules_version = self._safety().assess(_empty(), PatientContext()).rules_version
        return self._result(knowns, rules_version, state)

    async def find_doctors(
        self, query: DoctorQuery, cursor: str | None, limit: int
    ) -> Page[DoctorOption]:
        """NAV-10, NAV-11, NAV-13, NAV-15. Ranking never uses commercial input (NAV-3)."""
        names: list[Any] | None = None
        if query.name:
            names = await self._ports()["record"].search_practitioners(query.name, 20)
            if not names:
                return Page(items=[])
        candidates = await self._ports()["booking"].doctor_candidates(
            query.specialty,
            bounding_box(query.near, query.max_km),
            query.date_from,
            query.date_to,
            query.organization_id,
        )
        ids = sorted({c.practitioner_id for c in candidates}, key=str)
        cards = {
            c.practitioner_id: c for c in await self._ports()["record"].practitioner_cards(ids)
        }
        ranked = rank_doctors(query, candidates, cards, self._clock.now(), names)
        start = int(cursor) if cursor and cursor.isdigit() else 0
        limit = max(1, min(limit, MAX_DOCTORS))
        page = ranked[start : start + limit]
        more = start + limit < len(ranked)
        return Page(items=page, next_cursor=str(start + limit) if more else None)

    async def plan_trip(
        self, specialties: list[Specialty], near: GeoPoint, date_from: date, date_to: date
    ) -> list[Itinerary]:
        """NAV-12, NAV-16: only for two or more needs; a single need is never made a trip."""
        wanted = list(dict.fromkeys(specialties))
        if not 2 <= len(wanted) <= MAX_TRIP_NEEDS:
            return []
        box = bounding_box(near, TRIP_RADIUS_KM)
        booking = self._ports()["booking"]
        needs = [await booking.doctor_candidates(s, box, date_from, date_to) for s in wanted]
        return plan_trips(needs, near, date_from, date_to)

    def _screen(self, knowns: Knowns, context: PatientContext) -> SafetyAssessment:
        """The red-flag rules over everything the patient has reported, as fixed phrases, so a
        rule that needs two findings fires even when they came in different turns or answers."""
        phrases = [
            self.kb.concepts[c].screen
            for c in sorted(knowns.present)
            if c in self.kb.concepts and self.kb.concepts[c].screen
        ]
        text = ". ".join(p for p in phrases if p)
        assessment: SafetyAssessment = self._safety().assess(
            UntrustedText(value=text, source="system"), context
        )
        return assessment

    # ----- steps ---------------------------------------------------------------------------------

    def _step(
        self,
        knowns: Knowns,
        language: Language,
        rules_version: str,
        findings: tuple[Finding, ...],
        state: CaseState | None = None,
    ) -> NavigationStep:
        ask = next_question(self.kb, knowns)
        if ask is not None:
            return NavigationStep(question=self._question(ask, language), findings=findings)
        return NavigationStep(result=self._result(knowns, rules_version, state), findings=findings)

    def _question(self, ask: Ask, language: Language) -> NavigationQuestion:
        lang = language.value
        if ask.clarify is not None:
            q = self.kb.clarify[ask.clarify]
            return NavigationQuestion(
                concept=ask.clarify,
                question_key=f"nav.clarify.{ask.clarify}",
                text=q.text[lang],
                answer_type="choice",
                choices=tuple(option[lang] for option in q.choices.values()),
            )
        q = self.kb.questions[ask.concept]
        return NavigationQuestion(
            concept=ask.concept,
            question_key=f"nav.{ask.concept}",
            text=q.text[lang],
            answer_type="yes_no",
            red_flag_screen=ask.red_flag_screen,
        )

    def _result(
        self, knowns: Knowns, rules_version: str, state: CaseState | None
    ) -> NavigationResult:
        decision = decide(self.kb, knowns)
        sources = {f.key: f for f in state.facts} if state is not None else {}

        def finding(concept: str, present: bool) -> Finding:
            fact = sources.get(concept)
            ids = fact.provenance_ids if fact is not None else ()
            return Finding(concept=concept, present=present, provenance_id=ids[0] if ids else None)

        return NavigationResult(
            urgency=decision.urgency,
            urgency_floor=knowns.floor,
            primary_specialty=decision.primary,
            alternatives=tuple(
                SpecialtyOption(
                    specialty=o.specialty, probability=o.probability, reason_key=o.reason_key
                )
                for o in decision.alternatives
            ),
            confidence=decision.confidence,
            low_confidence=decision.low_confidence,
            findings=tuple(finding(c, True) for c in sorted(knowns.present)),
            negatives=tuple(finding(c, False) for c in sorted(knowns.absent)),
            questions_asked=knowns.counted,
            kb_version=self.kb.version,
            rules_version=rules_version,
        )

    # ----- what is known ------------------------------------------------------------------------

    def _knowns(self, state: CaseState, context: PatientContext, floor: Urgency | None) -> Knowns:
        present: set[str] = set()
        absent: set[str] = set()
        asked: set[str] = set()
        clarified: set[str] = set()
        floors = [floor, state.urgency_floor]
        for fact in state.facts:
            self._read_fact(fact, present, absent, asked, clarified, floors)
        present |= self._from_context(context)
        not_counted = self.kb.red_flag_screen | {c for f in self.kb.screens.values() for c in f}
        counted = len({c for c in asked if c in self.kb.questions and c not in not_counted})
        return Knowns(
            present=frozenset(present),
            absent=frozenset(absent - present),
            asked=frozenset(asked),
            clarified=frozenset(clarified),
            counted=counted,
            age_years=context.age_years,
            sex=context.sex,
            floor=max_urgency(*floors),
        )

    def _read_fact(
        self,
        fact: CaseFact,
        present: set[str],
        absent: set[str],
        asked: set[str],
        clarified: set[str],
        floors: list[Urgency | None],
    ) -> None:
        if fact.key.startswith("red_flag.") and fact.value in {u.value for u in Urgency}:
            floors.append(Urgency(str(fact.value)))
        elif fact.key in self.kb.clarify:
            clarified.add(fact.key)
            chosen = self._chosen(fact.key, fact.value)
            if chosen:
                present.add(chosen)
        elif fact.key in self.kb.concepts:
            if fact.value != MENTIONED:
                asked.add(fact.key)
            if fact.value == UNSURE:
                return
            (present if fact.present else absent).add(fact.key)

    def _apply(
        self, knowns: Knowns, concept: str, answer: str | float | bool
    ) -> tuple[Knowns, tuple[Finding, ...]]:
        """This answer, which the companion records itself after the step."""
        if concept in self.kb.clarify:
            chosen = self._chosen(concept, answer)
            present = knowns.present | ({chosen} if chosen else set())
            new = (Finding(concept=chosen, present=True),) if chosen else ()
            return replace(knowns, clarified=knowns.clarified | {concept}, present=present), new
        value = str(answer).casefold()
        asked = knowns.asked | {concept}
        counted = knowns.counted + (
            1
            if concept in self.kb.questions
            and concept not in self.kb.red_flag_screen
            and concept not in {c for f in self.kb.screens.values() for c in f}
            and concept not in knowns.asked
            else 0
        )
        if value in ("yes", "true"):
            return replace(
                knowns,
                present=knowns.present | {concept},
                absent=knowns.absent - {concept},
                asked=asked,
                counted=counted,
            ), ()
        if value in ("no", "false"):
            return replace(
                knowns,
                absent=knowns.absent | {concept},
                present=knowns.present - {concept},
                asked=asked,
                counted=counted,
            ), ()
        return replace(knowns, asked=asked, counted=counted), ()  # not sure: neither way

    def _chosen(self, name: str, value: object) -> str | None:
        """The concept a clarifying answer sets: by option key or its label in any language."""
        said = str(value).casefold().strip()
        for key, option in self.kb.clarify[name].choices.items():
            labels = {key, *(str(option.get(lang, "")) for lang in ("en", "si"))}
            if said in {label.casefold() for label in labels if label}:
                sets = option.get("sets")
                return str(sets) if sets else None
        return None

    def _from_context(self, context: PatientContext) -> set[str]:
        """Chronic conditions and pregnancy from the consented record and profile."""
        found = {
            c.id
            for c in self.kb.concepts.values()
            if c.chronic and any(code.startswith(c.chronic) for code in context.chronic_codes)
        }
        if context.pregnant and "pregnant" in self.kb.concepts:
            found.add("pregnant")
        return found

    async def _case(self, principal: Principal, case_id: CaseId) -> CaseState:
        state: CaseState = await self._ports()["cases"].get(principal, case_id)
        return state

    def _safety(self) -> Any:
        return self._ports()["safety"]


def _empty() -> UntrustedText:
    return UntrustedText(value="", source="system")
