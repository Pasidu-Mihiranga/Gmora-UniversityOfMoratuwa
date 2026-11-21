# ADR-0027: "Offline mode" and "test doubles" instead of "fake"

Status: Accepted (28 Nov 2026). Refines the naming in ADR-0002 and ADR-0007.

## Context

The word "fake" appeared in configuration (`MODEL_PROVIDER=fake`), in the UI label "Simulated AI"
and in test code (`FakeVerifier`). Readers outside engineering, including judges, can read it as
"the AI is fake".

## Decision

- The keyless provider is **offline mode** (`MODEL_PROVIDER=offline`): deterministic, scripted
  replies so the product runs without model keys.
- The UI label for its output is **"Demo mode · sample replies"** (si and en). It appears only when
  offline mode is active; the judged demo runs with real models, so it does not appear there.
- Test code uses **test doubles** with descriptive names (`InMemoryCaseService`, `ScriptedVerifier`,
  `KeywordSafetyService`) in `suwapath/contracts/testing/`, never bound outside tests.
- Internal data flags stay `simulated` (accurate and never shown to users).

## Consequences

Same honesty, clearer meaning. All specs, configuration and tests use the new names.

## Alternatives considered

Keeping "fake" (standard engineering jargon, misleading to others), removing the label (would hide
that replies are not from a real model, which is not acceptable).
