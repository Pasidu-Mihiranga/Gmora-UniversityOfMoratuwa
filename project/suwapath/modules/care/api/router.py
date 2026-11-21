"""HTTP routes for the `care` module. Stubs until issue M09 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import ALL_ROLES, StubRoute, stub_router

ROUTES = [
    StubRoute(
        "GET",
        "/patients/{patient_id}/doses",
        (Role.PATIENT, Role.GUARDIAN),
        "Doses for a day",
        "M09",
    ),
    StubRoute(
        "POST",
        "/patients/{patient_id}/doses/{dose_id}/taken",
        (Role.PATIENT,),
        "Mark a dose taken",
        "M09",
    ),
    StubRoute(
        "GET",
        "/patients/{patient_id}/adherence",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Adherence summary",
        "M09",
    ),
    StubRoute("GET", "/programmes", ALL_ROLES, "Programme catalogue", "M09"),
    StubRoute(
        "GET",
        "/programmes/{programme_key}/offerings",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Approved offerings nearby",
        "M09",
    ),
    StubRoute(
        "POST", "/patients/{patient_id}/enrollments", (Role.PATIENT,), "Join a programme", "M09"
    ),
    StubRoute(
        "GET",
        "/patients/{patient_id}/enrollments",
        (Role.PATIENT, Role.GUARDIAN, Role.DOCTOR),
        "Enrolments",
        "M09",
    ),
    StubRoute("POST", "/doctor/referrals", (Role.DOCTOR,), "Refer a patient to a programme", "M09"),
    StubRoute("GET", "/patients/{patient_id}/referrals", (Role.PATIENT,), "My referrals", "M09"),
    StubRoute(
        "POST", "/referrals/{referral_id}/accept", (Role.PATIENT,), "Accept a referral", "M09"
    ),
    StubRoute(
        "POST", "/referrals/{referral_id}/decline", (Role.PATIENT,), "Decline a referral", "M09"
    ),
    StubRoute(
        "GET", "/hospital/offerings", (Role.HOSPITAL_ADMIN,), "My hospital's offerings", "M09"
    ),
    StubRoute("POST", "/hospital/offerings", (Role.HOSPITAL_ADMIN,), "Create an offering", "M09"),
    StubRoute(
        "PATCH",
        "/hospital/offerings/{offering_id}",
        (Role.HOSPITAL_ADMIN,),
        "Edit or submit an offering",
        "M09",
    ),
    StubRoute("GET", "/admin/programmes", (Role.SYSTEM_ADMIN,), "Programme catalogue admin", "M09"),
    StubRoute(
        "PUT",
        "/admin/programmes/{programme_key}",
        (Role.SYSTEM_ADMIN,),
        "Enable or disable a programme",
        "M09",
    ),
    StubRoute(
        "GET", "/admin/offerings", (Role.SYSTEM_ADMIN,), "Offerings waiting for approval", "M09"
    ),
    StubRoute(
        "POST",
        "/admin/offerings/{offering_id}/decision",
        (Role.SYSTEM_ADMIN,),
        "Approve or reject",
        "M09",
    ),
    StubRoute("GET", "/guardian/dependents", (Role.GUARDIAN,), "Linked patients", "M09"),
    StubRoute("GET", "/guardian/alerts", (Role.GUARDIAN,), "Alerts", "M09"),
    StubRoute(
        "POST", "/guardian/alerts/{alert_id}/ack", (Role.GUARDIAN,), "Acknowledge an alert", "M09"
    ),
    StubRoute("GET", "/guardian/rules", (Role.GUARDIAN,), "Alert rules", "M09"),
    StubRoute("POST", "/guardian/rules", (Role.GUARDIAN,), "Create an alert rule", "M09"),
]

router = stub_router("care", ROUTES)
