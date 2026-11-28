"""Build `modules/navigation/kb/condition_groups.yaml` from DDXPlus (task D08, NAV-4).

Every number in the output is counted from the DDXPlus validate patients; none is written by hand:
- P(concept | group): the share of the group's patients who have the concept (any of its mapped
  evidences or pain regions), with add-one-half smoothing;
- prior P(group | age band, sex): the share of patients in that band and sex, add-one smoothing.

The test patients are not read here: they are kept for the evaluation (`13-evaluation.md`).

    python eval/scripts/fetch_ddxplus.py
    python eval/scripts/build_navigation_kb.py

DDXPlus: Fansi Tchango et al., "DDXPlus: A New Dataset For Automatic Medical Diagnosis",
NeurIPS 2022 Datasets and Benchmarks. CC BY 4.0. https://doi.org/10.6084/m9.figshare.20043374
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "eval" / "data" / "ddxplus"
KB = ROOT / "suwapath" / "modules" / "navigation" / "kb"
BANDS = ((0, 4, "0-4"), (5, 17, "5-17"), (18, 39, "18-39"), (40, 59, "40-59"), (60, 74, "60-74"))
SEXES = {"F": "female", "M": "male"}
NOWHERE = "V_123"


def band(age: int) -> str:
    return next((name for low, high, name in BANDS if low <= age <= high), "75+")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def region_values(evidences: dict[str, Any], regions: dict[str, list[str]]) -> dict[str, str]:
    """DDXPlus pain-location value code -> our body region, by the value's English meaning."""
    meanings = evidences["E_55"]["value_meaning"]
    found: dict[str, str] = {}
    for code, meaning in meanings.items():
        text = meaning["en"].casefold()
        for region, words in regions.items():
            if any(word in text for word in words):
                found[code] = region
                break
    return found


def concepts_of(
    evidence_list: list[str],
    index: dict[str, list[str]],
    regions: dict[str, str],
    by_region: dict[str, list[str]],
) -> set[str]:
    present: set[str] = set()
    for item in evidence_list:
        name, _, value = item.partition("_@_")
        if name == "E_57" and value == NOWHERE:
            continue  # "the pain does not spread" is not the concept
        present.update(index.get(name, ()))
        if name == "E_55" and value in regions:
            present.update(by_region.get(regions[value], ()))
    return present


def build(data: Path, kb: Path) -> dict[str, Any]:
    concepts = yaml.safe_load((kb / "concepts.yaml").read_text(encoding="utf-8"))
    evidences = json.loads((data / "release_evidences.json").read_text(encoding="utf-8"))
    conditions = json.loads((data / "release_conditions.json").read_text(encoding="utf-8"))
    index: dict[str, list[str]] = defaultdict(list)
    by_region: dict[str, list[str]] = defaultdict(list)
    for concept, entry in concepts["concepts"].items():
        for evidence in entry.get("ddx") or ():
            if evidence not in evidences:
                raise SystemExit(f"{concept}: unknown DDXPlus evidence {evidence}")
            index[evidence].append(concept)
        for region in entry.get("pain_regions") or ():
            by_region[region].append(concept)
    regions = region_values(evidences, concepts["pain_regions"])
    english = {v["cond-name-eng"]: k for k, v in conditions.items()}

    patients: Counter[str] = Counter()
    has: dict[str, Counter[str]] = defaultdict(Counter)
    prior: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    archive = zipfile.ZipFile(data / "release_validate_patients.zip")
    with archive, archive.open(archive.namelist()[0]) as raw:
        for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")):
            group = row["PATHOLOGY"]
            patients[group] += 1
            for concept in concepts_of(
                ast.literal_eval(row["EVIDENCES"]), index, regions, by_region
            ):
                has[group][concept] += 1
            prior[(band(int(row["AGE"])), SEXES[row["SEX"]])][group] += 1

    # Concepts with no DDXPlus mapping (the elder-care complaints) have no counts: not scored.
    names = sorted(
        c for c, e in concepts["concepts"].items() if e.get("ddx") or e.get("pain_regions")
    )
    groups = []
    for group in sorted(patients):
        source = conditions[english[group]]
        n = patients[group]
        groups.append(
            {
                "id": slug(group),
                "name_en": group,
                "icd10": source["icd10-id"].upper(),
                "ddxplus_severity": source["severity"],
                "patients": n,
                "likelihood": {c: round((has[group][c] + 0.5) / (n + 1), 4) for c in names},
            }
        )
    ids = {g["name_en"]: g["id"] for g in groups}
    priors: dict[str, dict[str, dict[str, float]]] = {}
    for (age_band, sex), counts in sorted(prior.items()):
        total = sum(counts.values())
        priors.setdefault(age_band, {})[sex] = {
            ids[g]: round((counts[g] + 1) / (total + len(groups)), 5) for g in sorted(patients)
        }
    return {
        "version": "condition-groups-v1",
        "source": "DDXPlus validate patients (CC BY 4.0), counted by "
        "eval/scripts/build_navigation_kb.py",
        "citation": "Fansi Tchango et al., DDXPlus, NeurIPS 2022 Datasets and Benchmarks; "
        "https://doi.org/10.6084/m9.figshare.20043374",
        "concepts_version": concepts["version"],
        "note": "Internal to the engine (NAV-2): groups choose questions and rank doctor types, "
        "and are never shown, stored as conditions or sent. DDXPlus priors are synthetic, not Sri "
        "Lankan epidemiology.",
        "patients": sum(patients.values()),
        "groups": groups,
        "priors": priors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--kb", type=Path, default=KB)
    args = parser.parse_args()
    result = build(args.data, args.kb)
    out = args.kb / "condition_groups.yaml"
    header = (
        "# Generated by eval/scripts/build_navigation_kb.py from DDXPlus. Do not edit by hand.\n"
    )
    out.write_text(
        header + yaml.safe_dump(result, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    print(f"Wrote {out} ({len(result['groups'])} groups, {result['patients']} patients)")


if __name__ == "__main__":
    main()
