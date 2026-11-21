"""HTTP routes for the `booking` module. Stubs until issue M08 implements them (W0.8)."""

from suwapath.contracts.enums import Role
from suwapath.platform.http.stubs import StubRoute, stub_router

ROUTES = [
    StubRoute(
        "GET",
        "/doctors/{practitioner_role_id}/slots",
        (Role.PATIENT, Role.GUARDIAN),
        "Free slots",
        "M08",
    ),
    StubRoute("POST", "/bookings/holds", (Role.PATIENT,), "Hold a slot", "M08"),
    StubRoute(
        "POST", "/bookings/holds/{hold_id}/confirm", (Role.PATIENT,), "Confirm a booking", "M08"
    ),
    StubRoute(
        "DELETE", "/bookings/{appointment_id}", (Role.PATIENT, Role.HOSPITAL_ADMIN), "Cancel", "M08"
    ),
    StubRoute("GET", "/bookings", (Role.PATIENT,), "My appointments", "M08"),
    StubRoute("GET", "/hospital/sessions", (Role.HOSPITAL_ADMIN,), "Sessions with counts", "M08"),
    StubRoute(
        "GET",
        "/hospital/sessions/{schedule_id}/appointments",
        (Role.HOSPITAL_ADMIN,),
        "Session appointments",
        "M08",
    ),
]

router = stub_router("booking", ROUTES)
