# ADR-0023: Programme catalogue owned by the platform, offerings by hospitals

Status: Accepted (27 Nov 2026)

## Context

Care programmes carry clinical safety content (red flags, schedules, alert rules) and connect
patients, guardians, doctors and hospitals.

## Decision

Programme types are code and reviewed content owned by the platform; platform admins enable them and
approve hospital listings. Hospital admins create offerings of enabled types (sessions, doctors,
capacity, fee, location). Doctors refer; patients accept or join and set guardian consent. Offerings
are ranked by distance and next session, never by fees.

## Consequences

Safety content is identical everywhere and cannot be edited by a hospital. Hospitals get a listing
product. New programmes are registrations.

## Alternatives considered

Hospitals defining their own programmes (unreviewed safety content), platform-only programmes with no
hospital offerings (loses the hospital connection and revenue).
