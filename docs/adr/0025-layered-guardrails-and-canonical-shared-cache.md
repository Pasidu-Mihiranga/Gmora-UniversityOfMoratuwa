# ADR-0025: Layered guardrails and a canonical-topic shared cache

Status: Accepted (27 Nov 2026)

## Context

An LLM-based health platform faces prompt injection, unsafe output rendering, excessive agency,
cache poisoning and ordinary web attacks at once. Controls were spread across several specs and a few
were missing.

## Decision

One security spec with: ordered authorization decision points and a route role table checked at
startup; an input guardrail pipeline (limits, normalization, safety screen, injection screen that
flags rather than blocks); output guardrails (schema, verifier, prompt-echo, reverse PHI check, tool
calls treated as outputs); a safe Markdown renderer with no HTML, no images and allowlisted links;
strict security headers, CSP, CORS, CSRF checks and `no-store` on all API responses; rate limits;
and a shared answer cache keyed by canonical knowledge-base topics whose answers are generated from
the canonical question, never the user's wording. Mapped to the OWASP Top 10 for LLM Applications.

## Consequences

Each risk has a named control and a test. The shared cache hits less often (only canonical topics)
but cannot be poisoned through crafted questions.

## Alternatives considered

Blocking suspected injections outright (would refuse real patients who write unusually), keying the
shared cache by user text (poisonable), relying on React escaping alone (misses links, images and
Markdown).
