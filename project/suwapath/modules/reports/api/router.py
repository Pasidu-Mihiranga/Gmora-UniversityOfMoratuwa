"""HTTP routes for the `reports` module. Stubs until issue M15 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute(
        "POST",
        "/patients/{patient_id}/reports",
        (Role.PATIENT, Role.DOCTOR),
        "Upload a report",
        "M15",
    ),
    StubRoute(
        "GET",
        "/patients/{patient_id}/reports",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Reports",
        "M15",
    ),
    StubRoute(
        "GET",
        "/reports/{report_id}",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Report with explanation",
        "M15",
    ),
]

router = stub_router("reports", ROUTES)
