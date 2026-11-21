# ADR-0019: LLM only when needed: cost tiers, intent classifier and two caches

Status: Accepted (27 Nov 2026)

## Context

Calling a chat model for every message is slow and costly. Many requests are answered by rules, by an
answer someone already received, or by a direct tool call.

## Decision

Resolve each request by the cheapest capable tier: rules, then caches, then a decision-model intent
classifier that calls tools directly, then the chat model with retrieval. Two caches: a shared cache
for general questions (answers with no personal data, verifier-approved) and an encrypted
user-scoped cache invalidated by source and consent fingerprints. Safety rules always run first and
are never cached.

## Consequences

Lower cost and latency, measured as tier mix and cost saved. Cache rules must be strict to avoid
serving stale or personal answers (stale user-cache hits must be zero).

## Alternatives considered

Always calling the chat model (simple, slow, expensive), a shared cache for all answers (would leak
personal answers between users).
