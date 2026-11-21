"""HTTP routes for the `consent` module. Stubs until issue M03 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute(
        "GET",
        "/patients/{patient_id}/consents",
        (Role.PATIENT,),
        "Grants per guardian and doctor",
        "M03",
    ),
    StubRoute(
        "PUT",
        "/patients/{patient_id}/consents/{grantee_id}",
        (Role.PATIENT,),
        "Set what a person may see",
        "M03",
    ),
    StubRoute(
        "DELETE",
        "/patients/{patient_id}/consents/{grantee_id}",
        (Role.PATIENT,),
        "Revoke access",
        "M03",
    ),
    StubRoute(
        "POST",
        "/rules/parse",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Plain-language rule to a proposal",
        "M03",
    ),
    StubRoute(
        "POST", "/rules", (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR), "Confirm a rule", "M03"
    ),
    StubRoute("GET", "/rules", (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR), "My rules", "M03"),
    StubRoute(
        "GET",
        "/settings",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "My assistant settings",
        "M03",
    ),
    StubRoute(
        "PATCH", "/settings", (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR), "Change settings", "M03"
    ),
    StubRoute(
        "POST", "/settings/suggestions", (Role.GUARDIAN,), "Suggest a setting to a patient", "M03"
    ),
    StubRoute(
        "POST",
        "/settings/suggestions/{suggestion_id}",
        (Role.PATIENT,),
        "Accept or decline a suggestion",
        "M03",
    ),
]

router = stub_router("consent", ROUTES)
