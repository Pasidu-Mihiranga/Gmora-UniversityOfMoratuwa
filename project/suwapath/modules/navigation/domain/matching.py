"""Doctor matching and trips (NAV-10 to NAV-13, NAV-15, NAV-16): pure, so the ranking is tested.

Ranking uses clinical fit (the caller asks per doctor type, primary first), continuity, the
earliest free slot, distance and fee, then the patient's language as a preference. Commissions,
booking fees and listing fees are not inputs at all (NAV-3): no field carries them here.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from suwapath.contracts.fhir.base import GeoPoint
from suwapath.contracts.ports.booking import BoundingBox, DoctorCandidate, SlotView
from suwapath.contracts.ports.navigation import DoctorOption, DoctorQuery, Itinerary, TripStop
from suwapath.contracts.ports.record import PractitionerCard
from suwapath.platform.clock import COLOMBO

KM_PER_DEGREE = 111.32
TRAVEL_KMH = 20.0  # NAV-12 and the same-day cutoff of NAV-13
TRIP_MAX_KM = 5.0
TRIP_GAP = timedelta(minutes=20)
TRIP_CANDIDATES = 5
TRIP_DAYS = 14
TRIP_RADIUS_KM = 25.0
MAX_TRIP_OPTIONS = 3
TIME_OF_DAY = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


def bounding_box(near: GeoPoint, km: float) -> BoundingBox:
    dlat = km / KM_PER_DEGREE
    dlng = km / (KM_PER_DEGREE * max(math.cos(math.radians(near.lat)), 0.01))
    return BoundingBox(
        south=near.lat - dlat, north=near.lat + dlat, west=near.lng - dlng, east=near.lng + dlng
    )


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat, dlng = lat2 - lat1, math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def travel(km: float) -> timedelta:
    return timedelta(hours=km / TRAVEL_KMH)


def _in_time_of_day(slot: SlotView, time_of_day: str) -> bool:
    if time_of_day not in TIME_OF_DAY:
        return True
    start, end = TIME_OF_DAY[time_of_day]
    return start <= slot.start.astimezone(COLOMBO).hour < end


@dataclass(frozen=True)
class Ranked:
    option: DoctorOption
    key: tuple[object, ...]


def rank_doctors(
    query: DoctorQuery,
    candidates: list[DoctorCandidate],
    cards: dict[object, PractitionerCard],
    now: datetime,
    name_matches: list[object] | None = None,
) -> list[DoctorOption]:
    """Filter to doctors within `max_km` (and matching the name, if one was given) that have a
    free slot the patient can still reach, then rank (NAV-10, NAV-13, NAV-15)."""
    ranked: list[Ranked] = []
    continuity = set(query.prefer_practitioner_ids)
    for candidate in candidates:
        if name_matches is not None and candidate.practitioner_id not in name_matches:
            continue
        distance = haversine_km(query.near, candidate.location)
        if distance > query.max_km:
            continue
        reachable = now + travel(distance)  # same day: only slots after the trip (NAV-13)
        slot = next(
            (
                s
                for s in candidate.free_slots
                if s.start >= reachable and _in_time_of_day(s, query.time_of_day)
            ),
            None,
        )
        if slot is None:
            continue
        card = cards.get(candidate.practitioner_id)
        languages = card.languages if card else ()
        known = candidate.practitioner_id in continuity
        option = DoctorOption(
            practitioner_id=candidate.practitioner_id,
            practitioner_role_id=candidate.practitioner_role_id,
            display_name=card.display_name if card else "",
            specialty=candidate.specialty,
            organization_id=candidate.organization_id,
            organization_name=candidate.organization_name,
            distance_km=round(distance, 1),
            next_slot_id=slot.slot_id,
            next_slot_at=slot.start,
            fee_lkr=candidate.fee_lkr,
            languages=tuple(languages),
            reason_key="nav.card.continuity" if known else "nav.card.specialty_fit",
            continuity=known,
        )
        speaks = query.language is None or query.language in languages
        name_rank = name_matches.index(candidate.practitioner_id) if name_matches else 0
        key = (
            name_rank,  # the doctor the patient asked for comes first
            not known,
            slot.start,
            round(distance, 1),
            candidate.fee_lkr,  # the price the patient pays, never a commission (NAV-3)
            not speaks,
            str(candidate.practitioner_role_id),
        )
        ranked.append(Ranked(option, key))
    return [r.option for r in sorted(ranked, key=lambda r: r.key)]


# ----- several doctors in one trip (NAV-12, NAV-16) -----------------------------------------------


@dataclass(frozen=True)
class Visit:
    candidate: DoctorCandidate
    slot: SlotView


def plan_trips(
    needs: list[list[DoctorCandidate]], near: GeoPoint, date_from: date, date_to: date
) -> list[Itinerary]:
    """Days on which every need can be seen, at one hospital or at hospitals under 5 km apart,
    with 20 minutes plus travel between visits. Bounded: 5 doctors per need, 14 days. Ranked by
    waiting plus travel time; at most 3, on different days."""
    last = min(date_to, date_from + timedelta(days=TRIP_DAYS - 1))
    pools = [
        sorted(
            (c for c in need if haversine_km(near, c.location) <= TRIP_RADIUS_KM),
            key=lambda c: (
                c.free_slots[0].start if c.free_slots else datetime.max.replace(tzinfo=COLOMBO),
                str(c.practitioner_role_id),
            ),
        )[:TRIP_CANDIDATES]
        for need in needs
    ]
    best: dict[date, tuple[int, Itinerary]] = {}
    for doctors in itertools.product(*pools):
        if not _close(doctors):
            continue
        for day in _days(date_from, last):
            found = _best_day(doctors, day)
            if found is None:
                continue
            score, itinerary = found
            if day not in best or score < best[day][0]:
                best[day] = (score, itinerary)
    ordered = sorted(best.values(), key=lambda pair: (pair[0], pair[1].day))
    return [itinerary for _, itinerary in ordered[:MAX_TRIP_OPTIONS]]


def _close(doctors: tuple[DoctorCandidate, ...]) -> bool:
    return all(
        haversine_km(a.location, b.location) < TRIP_MAX_KM
        for a, b in itertools.combinations(doctors, 2)
    )


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def _best_day(doctors: tuple[DoctorCandidate, ...], day: date) -> tuple[int, Itinerary] | None:
    best: tuple[int, Itinerary] | None = None
    for order in itertools.permutations(doctors):
        visits: list[Visit] = []
        wait = moved = timedelta(0)
        ready: datetime | None = None
        for doctor in order:
            trip = (
                travel(haversine_km(visits[-1].candidate.location, doctor.location))
                if visits
                else timedelta(0)
            )
            earliest = ready + trip if ready is not None else None
            slot = next(
                (
                    s
                    for s in doctor.free_slots
                    if s.start.astimezone(COLOMBO).date() == day
                    and (earliest is None or s.start >= earliest)
                ),
                None,
            )
            if slot is None:
                break
            if visits:
                moved += trip
                wait += slot.start - visits[-1].slot.start - TRIP_GAP - trip
            visits.append(Visit(doctor, slot))
            ready = slot.start + TRIP_GAP
        if len(visits) != len(doctors):
            continue
        minutes = int((wait + moved).total_seconds() // 60)
        itinerary = Itinerary(
            day=day,
            stops=tuple(
                TripStop(
                    slot_id=v.slot.slot_id,
                    practitioner_role_id=v.candidate.practitioner_role_id,
                    start=v.slot.start,
                )
                for v in visits
            ),
            total_wait_minutes=int(wait.total_seconds() // 60),
            total_travel_minutes=int(moved.total_seconds() // 60),
        )
        if best is None or minutes < best[0]:
            best = (minutes, itinerary)
    return best
