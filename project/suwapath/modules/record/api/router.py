"""HTTP routes for the `record` module. Stubs until issue M05 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute(
        "GET",
        "/patients/{patient_id}/summary",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Record summary filtered by access",
        "M05",
    ),
    StubRoute(
        "GET",
        "/patients/{patient_id}/resources",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Resources of one type",
        "M05",
    ),
    StubRoute(
        "GET", "/patients/{patient_id}/fhir/export", (Role.PATIENT,), "FHIR R4 Bundle", "M05"
    ),
    StubRoute(
        "GET",
        "/doctor/sources/{provenance_id}",
        (Role.DOCTOR,),
        "Source behind a brief line",
        "M05",
    ),
]

router = stub_router("record", ROUTES)
