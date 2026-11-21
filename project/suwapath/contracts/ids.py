"""Typed identifiers (CON-1). All IDs are UUIDv7, generated only by `platform.ids.new_id()`."""

from typing import NewType
from uuid import UUID

UserId = NewType("UserId", UUID)
PatientId = NewType("PatientId", UUID)
PractitionerId = NewType("PractitionerId", UUID)
PractitionerRoleId = NewType("PractitionerRoleId", UUID)
OrganizationId = NewType("OrganizationId", UUID)
CaseId = NewType("CaseId", UUID)
EventId = NewType("EventId", UUID)
MessageId = NewType("MessageId", UUID)
JobId = NewType("JobId", UUID)
ResourceId = NewType("ResourceId", UUID)
ConsentId = NewType("ConsentId", UUID)
AppointmentId = NewType("AppointmentId", UUID)
SlotId = NewType("SlotId", UUID)
ScheduleId = NewType("ScheduleId", UUID)
HoldId = NewType("HoldId", UUID)
DocumentId = NewType("DocumentId", UUID)
OfferingId = NewType("OfferingId", UUID)
ReferralId = NewType("ReferralId", UUID)
PrivateSessionId = NewType("PrivateSessionId", UUID)
TimerId = NewType("TimerId", UUID)

# W3C trace context trace id: 32 lowercase hex characters.
TraceId = NewType("TraceId", str)
