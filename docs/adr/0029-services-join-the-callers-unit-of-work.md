# ADR-0029: Services join the caller's unit of work

Status: Accepted (27 Nov 2026)

## Context

`EVT-1` requires that a use case writes its rows, case events, outbox events, timers and audit
entries in one transaction. Use cases often span modules: an agent job sends a message and appends a
case event; a subscriber reacts to `booking.appointment_booked` by changing the case. The ports in
`suwapath.contracts` take a `Principal` but no transaction, and changing every port signature would
couple every caller to the persistence layer.

## Decision

`UnitOfWork` records itself in a context variable while it is open. A service that implements a port
opens its writes with `uow_factory.join()`:

- inside an open unit of work, it joins that transaction under a savepoint, and the caller's
  `commit()` saves both; a failure inside the joined block rolls back only that block;
- with no unit of work open (for example an API route), it starts its own and commits on success.

Port signatures do not change. Code that owns a use case still opens `uow_factory()` and commits.

## Consequences

Cross-module use cases are atomic without passing sessions through ports. A service must not keep
a unit of work beyond one call. Concurrent tasks each get their own context, so a background task
does not join a request's transaction by accident.

## Alternatives considered

A transaction argument on every port method (explicit, but changes every port and double), and one
transaction per service call (simple, but breaks `EVT-1`: a crash could save a message without its
case event).
