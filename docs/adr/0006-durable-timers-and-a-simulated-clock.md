# ADR-0006: Durable timers and a simulated clock

Status: Accepted (27 Nov 2026)

## Context

Missed doses, the 4 hour approval timeout and check-in deadlines must survive restarts and be demoable in minutes.

## Decision

Timers are database rows fired by the worker. All code reads time from a Clock port; a simulated clock with a shared offset can be advanced in development and demo.

## Consequences

The guardian story and timeouts can be shown live and evaluated over simulated months.

## Alternatives considered

APScheduler in memory (lost on restart, cannot fast-forward).
