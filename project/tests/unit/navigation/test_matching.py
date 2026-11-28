"""Doctor matching and trips (NAV-10 to NAV-13, NAV-15): distance, filters, ranking order, and
the bounded trip search."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from suwapath.contracts.enums import Language, Specialty
from suwapath.contracts.fhir.base import GeoPoint
from suwapath.contracts.ids import (
    OrganizationId,
    PractitionerId,
    PractitionerRoleId,
    ScheduleId,
    SlotId,
)
from suwapath.contracts.ports.booking import DoctorCandidate, SlotView
from suwapath.contracts.ports.navigation import DoctorQuery
from suwapath.contracts.ports.record import PractitionerCard
from suwapath.modules.navigation.domain.matching import (
    bounding_box,
    haversine_km,
    plan_trips,
    rank_doctors,
)

HOME = GeoPoint(lat=6.9271, lng=79.8612)  # Colombo
NOW = datetime(2026, 11, 28, 3, 0, tzinfo=UTC)  # 08:30 in Colombo
DAY = date(2026, 11, 28)


def at(hours: float) -> datetime:
    return NOW + timedelta(hours=hours)


def doctor(
    *slots: datetime,
    km_north: float = 1.0,
    fee: int = 2000,
    practitioner: PractitionerId | None = None,
    org: OrganizationId | None = None,
    specialty: Specialty = Specialty.GENERAL_MEDICINE,
) -> DoctorCandidate:
    role = PractitionerRoleId(uuid4())
    return DoctorCandidate(
        practitioner_role_id=role,
        practitioner_id=practitioner or PractitionerId(uuid4()),
        specialty=specialty,
        organization_id=org or OrganizationId(uuid4()),
        organization_name="Hospital",
        location=GeoPoint(lat=HOME.lat + km_north / 111.32, lng=HOME.lng),
        fee_lkr=fee,
        free_slots=tuple(
            SlotView(
                slot_id=SlotId(uuid4()),
                schedule_id=ScheduleId(uuid4()),
                practitioner_role_id=role,
                number=n + 1,
                start=s,
                free=True,
            )
            for n, s in enumerate(slots)
        ),
    )


def query(**extra: object) -> DoctorQuery:
    base: dict[str, object] = {
        "specialty": Specialty.GENERAL_MEDICINE,
        "near": HOME,
        "date_from": DAY,
        "date_to": DAY + timedelta(days=2),
    }
    return DoctorQuery(**{**base, **extra})  # type: ignore[arg-type]


def test_distance_and_the_bounding_box() -> None:
    kandy = GeoPoint(lat=7.2906, lng=80.6337)
    assert 90 < haversine_km(HOME, kandy) < 100
    box = bounding_box(HOME, 25)
    assert box.south < HOME.lat < box.north and box.west < HOME.lng < box.east
    edge = GeoPoint(lat=HOME.lat + 24 / 111.32, lng=HOME.lng)
    assert box.south < edge.lat < box.north


def test_the_earliest_slot_wins_then_distance_then_fee() -> None:
    late, early = doctor(at(5)), doctor(at(2))
    near, far = doctor(at(3), km_north=1), doctor(at(3), km_north=8)
    cheap, dear = doctor(at(4), fee=1500), doctor(at(4), fee=3000)
    ranked = rank_doctors(query(), [late, far, dear, early, near, cheap], {}, NOW)
    order = [o.practitioner_role_id for o in ranked]
    assert order == [c.practitioner_role_id for c in (early, near, far, cheap, dear, late)]


def test_continuity_comes_before_an_earlier_slot() -> None:
    known = PractitionerId(uuid4())
    mine, other = doctor(at(6), practitioner=known), doctor(at(1))
    ranked = rank_doctors(query(prefer_practitioner_ids=(known,)), [other, mine], {}, NOW)
    assert ranked[0].practitioner_id == known and ranked[0].continuity
    assert ranked[0].reason_key == "nav.card.continuity"


def test_the_patients_language_is_preferred_not_required() -> None:
    si_id, en_id = PractitionerId(uuid4()), PractitionerId(uuid4())
    sinhala, english = doctor(at(2), practitioner=si_id), doctor(at(2), practitioner=en_id)
    cards = {
        si_id: PractitionerCard(practitioner_id=si_id, display_name="A", languages=(Language.SI,)),
        en_id: PractitionerCard(practitioner_id=en_id, display_name="B", languages=(Language.EN,)),
    }
    ranked = rank_doctors(query(language=Language.SI), [english, sinhala], cards, NOW)
    assert [o.practitioner_id for o in ranked] == [si_id, en_id]  # both shown, Sinhala first


def test_too_far_no_slot_or_unreachable_today_is_left_out() -> None:
    far = doctor(at(2), km_north=40)
    none = doctor()
    unreachable = doctor(at(0.25), km_north=20)  # 20 km at 20 km/h needs an hour
    reachable = doctor(at(1.5), km_north=20)
    ranked = rank_doctors(query(), [far, none, unreachable, reachable], {}, NOW)
    assert [o.practitioner_role_id for o in ranked] == [reachable.practitioner_role_id]


def test_time_of_day_filters_slots() -> None:
    morning, evening = doctor(at(1)), doctor(at(11))  # 09:30 and 19:30 in Colombo
    ranked = rank_doctors(query(time_of_day="evening"), [morning, evening], {}, NOW)
    assert [o.practitioner_role_id for o in ranked] == [evening.practitioner_role_id]


def test_a_named_doctor_is_found_first_and_only_named_ones() -> None:
    silva = PractitionerId(uuid4())
    asked, other = doctor(at(5), practitioner=silva), doctor(at(1))
    ranked = rank_doctors(query(name="Dr Silva"), [other, asked], {}, NOW, [silva])
    assert [o.practitioner_id for o in ranked] == [silva]


def test_the_fee_never_beats_an_earlier_slot() -> None:
    expensive_early, cheap_late = doctor(at(1), fee=9000), doctor(at(3), fee=500)
    ranked = rank_doctors(query(), [cheap_late, expensive_early], {}, NOW)
    assert ranked[0].practitioner_role_id == expensive_early.practitioner_role_id


# ----- trips (NAV-12) -----------------------------------------------------------------------------


def test_two_doctors_at_one_hospital_in_one_day_with_a_gap() -> None:
    org = OrganizationId(uuid4())
    heart = doctor(at(2), specialty=Specialty.CARDIOLOGY, org=org)
    sugar = doctor(at(2.1), at(3), specialty=Specialty.ENDOCRINOLOGY, org=org)
    [trip] = plan_trips([[heart], [sugar]], HOME, DAY, DAY + timedelta(days=13))
    first, second = trip.stops
    assert trip.day == DAY and second.start - first.start >= timedelta(minutes=20)
    assert trip.total_travel_minutes == 0


def test_hospitals_far_apart_make_no_trip() -> None:
    heart = doctor(at(2), specialty=Specialty.CARDIOLOGY, km_north=0)
    sugar = doctor(at(4), specialty=Specialty.ENDOCRINOLOGY, km_north=9)
    assert plan_trips([[heart], [sugar]], HOME, DAY, DAY + timedelta(days=13)) == []


def test_at_most_three_trips_on_different_days_within_14_days() -> None:
    org = OrganizationId(uuid4())
    days = [at(2 + 24 * n) for n in range(20)]
    heart = doctor(*days, specialty=Specialty.CARDIOLOGY, org=org)
    sugar = doctor(
        *(d + timedelta(hours=1) for d in days), specialty=Specialty.ENDOCRINOLOGY, org=org
    )
    trips = plan_trips([[heart], [sugar]], HOME, DAY, DAY + timedelta(days=30))
    assert len(trips) == 3 and len({t.day for t in trips}) == 3
    assert all(t.day <= DAY + timedelta(days=13) for t in trips)
