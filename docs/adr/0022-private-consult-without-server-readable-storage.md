# ADR-0022: Private consult without server-readable storage

Status: Accepted (27 Nov 2026)

## Context

Sexual-health questions are the most sensitive data the platform will see. A PIN-derived key alone is
weak (low entropy) and cannot hide content from the server while a model processes it.

## Decision

The server processes each turn in memory and never writes message text anywhere. Optional history
(opt-in, at most 7 days) is encrypted in the browser with a random 256-bit key; the server stores only
ciphertext. The key stays on the device, wrapped by a PIN-derived key, with a one-time recovery code.
Model processing is an explicit, disclosed session action with a zero-retention provider and no
identifiers. Audit records only that a session started and ended.

## Consequences

A server or backup breach reveals no consult content. Deletion is cryptographic. The model provider
is inside the privacy boundary during processing, which users are told. Someone holding an unlocked
device can read saved history; the default is to save nothing.

## Alternatives considered

Server-side encryption with the patient key (server could read it), PIN-only encryption on the server
(guessable offline), no model at all (no useful answers).
