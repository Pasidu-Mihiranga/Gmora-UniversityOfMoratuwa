"""Download the DDXPlus files the navigation knowledge base is built from (D08), and check them.

DDXPlus (CC BY 4.0): Fansi Tchango et al., "DDXPlus: A New Dataset For Automatic Medical
Diagnosis", NeurIPS 2022 Datasets and Benchmarks. https://doi.org/10.6084/m9.figshare.20043374

The files go to eval/data/ddxplus/, which is not committed. Each download is checked against the
SHA-256 recorded here, so everyone builds the same knowledge base.

    python eval/scripts/fetch_ddxplus.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "ddxplus"
FILES = {
    "release_conditions.json": (
        "https://ndownloader.figshare.com/files/62657140",
        "960112db157169215c5a5c1eac0993317d2363c0cf2dadf173735dc503810d48",
    ),
    "release_evidences.json": (
        "https://ndownloader.figshare.com/files/40495562",
        "980f7020ec068b290b49cca7f2ab0a2058faae37f9a94b113e10b6fe0c882f31",
    ),
    "release_validate_patients.zip": (  # builds the knowledge base
        "https://ndownloader.figshare.com/files/40495571",
        "625a96855becf0efe5da339fd3d440aa99597d48f784957a97dacc3fceaa7e40",
    ),
    "release_test_patients.zip": (  # evaluation only, never used to build
        "https://ndownloader.figshare.com/files/40495565",
        "ad3bb981f3453fd7abffb35cc595a1c02197e409c581bff97c1265c33eb00d23",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, (url, expected) in FILES.items():
        path = DATA / name
        if not path.exists() or sha256(path) != expected:
            print(f"downloading {name}")
            # Fixed https URLs from FILES only, never user input.
            request = urllib.request.Request(url, headers={"User-Agent": "dataset-fetch"})  # noqa: S310
            with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
                path.write_bytes(response.read())
        ok = sha256(path) == expected
        failed |= not ok
        print(f"{'ok' if ok else 'CHECKSUM MISMATCH'}  {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
