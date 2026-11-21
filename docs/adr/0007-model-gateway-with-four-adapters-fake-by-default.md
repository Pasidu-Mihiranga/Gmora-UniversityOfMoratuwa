# ADR-0007: Model gateway with four adapters, fake by default

Status: Accepted (27 Nov 2026)

## Context

Teammates and judges may have no API keys; we must not depend on one provider.

## Decision

All model calls go through one gateway: gemini (Vertex), openai_compatible (OpenAI, OpenRouter, Groq, Ollama), decisions (Jev), fake. Capabilities declared and checked. Budgets, retries, PHI minimization and cost logging in the gateway.

## Consequences

Clone and run with no keys. Fake output is always labelled. Evaluation refuses fake.

## Alternatives considered

Calling SDKs directly from agents (lock-in, no central budget or privacy control).
