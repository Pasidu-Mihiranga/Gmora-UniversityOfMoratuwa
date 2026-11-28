"""The navigation engine (spec 08 engine steps 2 to 7, NAV-1, NAV-2, NAV-5): pure, no I/O.

From what is known about the patient it either chooses the next question or decides how soon and
which kind of doctor. Condition groups are internal: they only weigh questions and doctor types,
and never leave this module (NAV-2).

Order of questions:
1. a clarifying question for an ambiguous finding ("dizzy": spinning or about to faint);
2. red-flag follow-ups that complete a safety rule (asked first, never counted, NAV-1);
3. the question with the highest expected information gain over doctor type and urgency.
Stop at a confident doctor type (0.75) and settled urgency (0.8), after 6 counted questions, when
the patient asks to stop, or when no question would help.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace

from suwapath.contracts.enums import Specialty, Urgency, max_urgency
from suwapath.modules.navigation.domain.kb import KnowledgeBase

SPECIALTY_STOP = 0.75
URGENCY_STOP = 0.8
MAX_QUESTIONS = 6
SOONER = 0.2  # any more urgent window at least this likely wins (errs toward sooner care)
MAX_ALTERNATIVES = 3
MIN_GAIN = 0.01
BANDS = ((0, 4, "0-4"), (5, 17, "5-17"), (18, 39, "18-39"), (40, 59, "40-59"), (60, 74, "60-74"))


@dataclass(frozen=True)
class Knowns:
    present: frozenset[str] = frozenset()
    absent: frozenset[str] = frozenset()
    asked: frozenset[str] = frozenset()  # asked, whatever the answer (including "not sure")
    clarified: frozenset[str] = frozenset()  # ambiguity checks already answered
    counted: int = 0  # questions that count toward the cap
    age_years: int | None = None
    sex: str = "unknown"
    floor: Urgency | None = None
    stop: bool = False

    def known(self, concept: str) -> bool:
        return concept in self.present or concept in self.absent or concept in self.asked


@dataclass(frozen=True)
class Ask:
    concept: str
    red_flag_screen: bool = False
    clarify: str | None = None  # the ambiguity name, for a clarifying question


@dataclass(frozen=True)
class Option:
    specialty: Specialty
    probability: float
    reason_key: str


@dataclass(frozen=True)
class Decision:
    urgency: Urgency
    primary: Specialty
    alternatives: tuple[Option, ...]
    confidence: float
    low_confidence: bool


def age_band(age: int) -> str:
    return next((name for low, high, name in BANDS if low <= age <= high), "75+")


def _prior(kb: KnowledgeBase, knowns: Knowns) -> dict[str, float]:
    bands = [age_band(knowns.age_years)] if knowns.age_years is not None else list(kb.priors)
    sexes = [knowns.sex] if knowns.sex in ("female", "male") else ["female", "male"]
    total: dict[str, float] = defaultdict(float)
    tables = [kb.priors[b][s] for b in bands for s in sexes if b in kb.priors and s in kb.priors[b]]
    for table in tables:
        for group, p in table.items():
            total[group] += p / len(tables)
    return total


def posterior(kb: KnowledgeBase, knowns: Knowns) -> dict[str, float]:
    """Naive Bayes over condition groups: prior times the likelihood of each present and each
    absent finding. Findings that were never asked count for nothing either way."""
    prior = _prior(kb, knowns)
    scored = kb.scored
    present, absent = knowns.present & scored, knowns.absent & scored
    logs = {}
    for group in kb.groups:
        value = math.log(prior.get(group.id, 1e-9))
        value += sum(math.log(group.likelihood[c]) for c in present)
        value += sum(math.log(1.0 - group.likelihood[c]) for c in absent)
        logs[group.id] = value
    top = max(logs.values())
    weights = {g: math.exp(v - top) for g, v in logs.items()}
    total = sum(weights.values())
    return {g: w / total for g, w in weights.items()}


def distributions(
    kb: KnowledgeBase, post: dict[str, float]
) -> tuple[dict[Specialty, float], dict[Urgency, float]]:
    """Groups to doctor types (each group's first doctor type) and to urgency windows."""
    specialty: dict[Specialty, float] = defaultdict(float)
    urgency: dict[Urgency, float] = defaultdict(float)
    for group in kb.groups:
        p = post.get(group.id, 0.0)
        specialty[group.specialties[0]] += p
        urgency[group.urgency] += p
    return dict(specialty), dict(urgency)


def _entropy(probabilities: list[float]) -> float:
    return -sum(p * math.log(p) for p in probabilities if p > 0)


def _uncertainty(kb: KnowledgeBase, knowns: Knowns) -> float:
    spec, urg = distributions(kb, posterior(kb, knowns))
    return _entropy(list(spec.values())) + _entropy(list(urg.values()))


def gain(kb: KnowledgeBase, knowns: Knowns, concept: str) -> float:
    """Expected drop in doctor-type plus urgency entropy from asking about `concept`."""
    post = posterior(kb, knowns)
    p_yes = sum(post[g.id] * g.likelihood[concept] for g in kb.groups)
    yes = replace(knowns, present=knowns.present | {concept})
    no = replace(knowns, absent=knowns.absent | {concept})
    expected = p_yes * _uncertainty(kb, yes) + (1 - p_yes) * _uncertainty(kb, no)
    return _uncertainty(kb, knowns) - expected


def _complaint(kb: KnowledgeBase, knowns: Knowns) -> str | None:
    """The first reviewed complaint rule whose complaint the patient has, if any."""
    return next((c.id for c in kb.complaints if c.concepts & knowns.present), None)


def confident(kb: KnowledgeBase, knowns: Knowns) -> bool:
    if not knowns.present & kb.scored:
        return False  # nothing scored yet: the prior alone is never a reason to stop
    spec, urg = distributions(kb, posterior(kb, knowns))
    return max(spec.values()) >= SPECIALTY_STOP and max(urg.values()) >= URGENCY_STOP


def next_question(kb: KnowledgeBase, knowns: Knowns) -> Ask | None:
    if knowns.stop:
        return None
    for concept in sorted(knowns.present):  # 1. the ambiguity check
        name = kb.concepts[concept].ambiguous if concept in kb.concepts else None
        if name and name not in knowns.clarified:
            return Ask(concept, clarify=name)
    for trigger in sorted(knowns.present & set(kb.screens)):  # 2. red-flag follow-ups (NAV-1)
        for follow_up in kb.screens[trigger]:
            if not knowns.known(follow_up):
                return Ask(follow_up, red_flag_screen=True)
    if knowns.counted >= MAX_QUESTIONS or confident(kb, knowns):
        return None
    if _complaint(kb, knowns) and not knowns.present & kb.scored:
        return None  # a reviewed complaint rule decides; nothing scored to refine
    candidates = [
        c for c in kb.scored if c in kb.questions and kb.concepts[c].askable and not knowns.known(c)
    ]
    if not candidates:
        return None
    best = max(sorted(candidates), key=lambda c: gain(kb, knowns, c))
    if gain(kb, knowns, best) < MIN_GAIN:
        return None
    return Ask(best, red_flag_screen=best in kb.red_flag_screen)


def decide(kb: KnowledgeBase, knowns: Knowns) -> Decision:
    """Step 7. Urgency = max(red-flag floor, most likely window, any more urgent window with
    probability at least 0.2). Doctor type: the top one at 0.75 or more, otherwise a general
    physician first with up to 3 alternatives (NAV-2). A child under 12 sees a children's doctor
    first. With nothing scored, a reviewed complaint rule or a general physician decides."""
    child = knowns.age_years is not None and knowns.age_years < kb.child_under
    if not knowns.present & kb.scored:
        return _without_scores(kb, knowns, child)
    spec, urg = distributions(kb, posterior(kb, knowns))
    likely = max(urg, key=lambda u: (urg[u], u.rank))
    sooner = [u for u, p in urg.items() if p >= SOONER and u.rank > likely.rank]
    urgency = max_urgency(knowns.floor, likely, *sooner) or likely
    ranked = sorted(spec.items(), key=lambda kv: (-kv[1], kv[0].value))
    top, confidence = ranked[0]
    complaint = _complaint(kb, knowns)
    if confidence < SPECIALTY_STOP and complaint:
        rule = next(c for c in kb.complaints if c.id == complaint)
        urgency = max_urgency(urgency, rule.urgency) or urgency
        reason = f"nav.reason.complaint.{rule.id}"
        return _route(kb, rule.specialties, urgency, confidence, child, reason, spec)
    if confidence >= SPECIALTY_STOP:
        return _route(kb, (top,), urgency, confidence, child, "nav.reason.symptoms_fit", spec)
    others = tuple(s for s, _ in ranked if s is not Specialty.GENERAL_MEDICINE)
    choice = (Specialty.GENERAL_MEDICINE, *others)
    return _route(kb, choice, urgency, confidence, child, "nav.reason.symptoms_fit", spec)


def _without_scores(kb: KnowledgeBase, knowns: Knowns, child: bool) -> Decision:
    complaint = _complaint(kb, knowns)
    if complaint:
        rule = next(c for c in kb.complaints if c.id == complaint)
        urgency = max_urgency(knowns.floor, rule.urgency) or rule.urgency
        reason = f"nav.reason.complaint.{rule.id}"
        return _route(kb, rule.specialties, urgency, 0.0, child, reason, {})
    urgency = max_urgency(knowns.floor, Urgency.ROUTINE) or Urgency.ROUTINE
    return _route(kb, (Specialty.GENERAL_MEDICINE,), urgency, 0.0, child, "", {})


def _route(
    kb: KnowledgeBase,
    specialties: tuple[Specialty, ...],
    urgency: Urgency,
    confidence: float,
    child: bool,
    reason: str,
    spec: dict[Specialty, float],
) -> Decision:
    order = list(dict.fromkeys(((kb.child_specialty,) if child else ()) + specialties))
    primary, rest = order[0], order[1 : 1 + MAX_ALTERNATIVES]
    options = tuple(
        Option(
            specialty=s,
            probability=round(spec.get(s, 0.0), 4),
            reason_key="nav.reason.common_start"
            if s is Specialty.GENERAL_MEDICINE
            else (reason or "nav.reason.also_fits"),
        )
        for s in rest
    )
    return Decision(urgency, primary, options, round(confidence, 4), confidence < SPECIALTY_STOP)
