"""Navigation engine check on held-out DDXPlus test patients (not used to build the KB).

Each simulated patient starts with their initial evidence (what they would say first) and answers
every question truthfully from their own evidence list; age and sex are known, as from the
profile. The engine runs to its stop rule. We report, against the routing labels of each
patient's true condition:

- right doctor type first time (the primary equals the label's first doctor type);
- doctor type among the cards shown (primary or one of up to 3 alternatives);
- under-triage (the window is less urgent than the label) and emergency recall;
- questions asked (counted toward the cap).

The routing labels are the D01 draft until the team labels replace them, and the red-flag rules
are not applied here (engine alone), so these numbers are an engineering check, not the KPI of
`13-evaluation.md` (NAV-9).

    python eval/scripts/eval_navigation.py --patients 3000
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import random
import sys
import zipfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from build_navigation_kb import concepts_of, region_values, slug  # noqa: E402

from suwapath.contracts.enums import Urgency  # noqa: E402
from suwapath.modules.navigation.domain.engine import Knowns, decide, next_question  # noqa: E402
from suwapath.modules.navigation.domain.kb import KnowledgeBase  # noqa: E402

DATA = ROOT / "eval" / "data" / "ddxplus"
SEXES = {"F": "female", "M": "male"}


def mapping(kb_folder: Path) -> tuple[dict[str, list[str]], dict[str, str], dict[str, list[str]]]:
    concepts = yaml.safe_load((kb_folder / "concepts.yaml").read_text(encoding="utf-8"))
    evidences = json.loads((DATA / "release_evidences.json").read_text(encoding="utf-8"))
    index: dict[str, list[str]] = {}
    by_region: dict[str, list[str]] = {}
    for concept, entry in concepts["concepts"].items():
        for evidence in entry.get("ddx") or ():
            index.setdefault(evidence, []).append(concept)
        for region in entry.get("pain_regions") or ():
            by_region.setdefault(region, []).append(concept)
    return index, region_values(evidences, concepts["pain_regions"]), by_region


def run(patients: int, seed: int) -> dict[str, object]:
    kb = KnowledgeBase.load()
    index, regions, by_region = mapping(ROOT / "suwapath" / "modules" / "navigation" / "kb")
    routes = {g.id: g for g in kb.groups}
    archive = zipfile.ZipFile(DATA / "release_test_patients.zip")
    with archive, archive.open(archive.namelist()[0]) as raw:
        rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")))
    random.Random(seed).shuffle(rows)  # noqa: S311 (a reproducible sample, not security)
    totals: Counter[str] = Counter()
    by_label: Counter[str] = Counter()
    questions: list[int] = []
    for row in rows[:patients]:
        group = routes[slug(row["PATHOLOGY"])]
        truth = concepts_of(ast.literal_eval(row["EVIDENCES"]), index, regions, by_region)
        first = concepts_of([row["INITIAL_EVIDENCE"]], index, regions, by_region)
        age = int(row["AGE"])
        knowns = Knowns(present=frozenset(first & truth), age_years=age, sex=SEXES[row["SEX"]])
        for _ in range(40):
            ask = next_question(kb, knowns)
            if ask is None:
                break
            if ask.clarify:
                knowns = replace(knowns, clarified=knowns.clarified | {ask.clarify})
                continue
            yes = ask.concept in truth
            knowns = replace(
                knowns,
                present=knowns.present | ({ask.concept} if yes else set()),
                absent=knowns.absent | (set() if yes else {ask.concept}),
                asked=knowns.asked | {ask.concept},
                counted=knowns.counted + (0 if ask.red_flag_screen else 1),
            )
        decision = decide(kb, knowns)
        child = age < kb.child_under
        expected = kb.child_specialty if child else group.specialties[0]
        shown = {decision.primary, *(o.specialty for o in decision.alternatives)}
        totals["patients"] += 1
        totals["right_first_time"] += decision.primary is expected
        totals["among_cards"] += expected in shown
        totals["under_triage"] += decision.urgency.rank < group.urgency.rank
        totals["over_triage"] += decision.urgency.rank > group.urgency.rank
        if group.urgency is Urgency.EMERGENCY:
            totals["emergencies"] += 1
            totals["emergency_caught"] += decision.urgency is Urgency.EMERGENCY
        totals["low_confidence"] += decision.low_confidence
        by_label[f"{expected.value}:{'ok' if decision.primary is expected else 'miss'}"] += 1
        questions.append(knowns.counted)
    n = totals["patients"]
    return {
        "patients": n,
        "kb_version": kb.version,
        "right_doctor_type_first_time": round(totals["right_first_time"] / n, 4),
        "doctor_type_among_cards": round(totals["among_cards"] / n, 4),
        "under_triage_rate": round(totals["under_triage"] / n, 4),
        "over_triage_rate": round(totals["over_triage"] / n, 4),
        "emergency_recall": round(totals["emergency_caught"] / max(1, totals["emergencies"]), 4),
        "low_confidence_rate": round(totals["low_confidence"] / n, 4),
        "mean_questions": round(sum(questions) / n, 2),
        "max_questions": max(questions),
        "by_doctor_type": dict(sorted(by_label.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patients", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    report = run(args.patients, args.seed)
    out = ROOT / "eval" / "runs" / "navigation_ddxplus_test.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "by_doctor_type"}, indent=2))


if __name__ == "__main__":
    main()
