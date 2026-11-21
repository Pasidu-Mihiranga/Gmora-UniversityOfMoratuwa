"""Module ports: one Protocol per module service (ARC-2, ARC-3).

Modules depend on these, never on each other. `bootstrap.py` binds the real implementations;
`suwapath.contracts.testing` provides in-memory fakes that pass the same contract suites (QA-1).
Every method that touches patient data takes the caller's `Principal` (SEC-11).
"""
