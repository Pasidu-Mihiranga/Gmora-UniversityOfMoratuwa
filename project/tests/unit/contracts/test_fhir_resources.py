from datetime import date

import pytest

from suwapath.contracts.fhir.base import age_band
from suwapath.contracts.fhir.resources import CLINICAL_RESOURCES, Consent, Provenance


@pytest.mark.parametrize("resource", CLINICAL_RESOURCES, ids=lambda r: r.resource_type)
def test_encrypted_fields_exist_on_the_model(resource: type) -> None:
    missing = resource.encrypted_fields - set(resource.model_fields)
    assert not missing, f"{resource.resource_type} declares unknown encrypted fields {missing}"


@pytest.mark.parametrize(
    "resource",
    [r for r in CLINICAL_RESOURCES if r not in (Provenance,)],
    ids=lambda r: r.resource_type,
)
def test_clinical_resources_declare_a_consent_category(resource: type) -> None:
    assert resource.category is not None


def test_consent_resource_has_no_category_of_its_own() -> None:
    assert Consent.category is None


@pytest.mark.parametrize(
    ("born", "on", "band"),
    [
        (date(2022, 6, 1), date(2026, 5, 31), "0-4"),
        (date(2021, 11, 27), date(2026, 11, 27), "5-17"),
        (date(1954, 1, 1), date(2026, 11, 27), "60-74"),
        (date(1951, 11, 28), date(2026, 11, 27), "60-74"),
        (date(1951, 11, 27), date(2026, 11, 27), "75+"),
    ],
)
def test_age_band_boundaries(born: date, on: date, band: str) -> None:
    assert age_band(born, on) == band
