# ADR-0020: Knowledge-driven navigation engine with information-gain questioning

Status: Accepted (27 Nov 2026)

## Context

The core promise is sending each patient to the right kind of doctor at the right time, without
diagnosing. LLM-only symptom checking under-triages emergencies (the proposal cites 52% for one
product). Doctors reason backwards from findings to hypotheses and ask questions that separate them.

## Decision

A deterministic engine over a versioned knowledge graph (DDXPlus knowledge base mapped to our
concepts, plus a referenced elder-care extension and team-reviewed routing labels). Red flags are
screened first and never capped. Naive Bayes scoring over hidden condition groups, questions chosen
by expected information gain over specialty and urgency, stopping at confidence 0.75 or 6 questions.
Low confidence routes to a general physician first with up to 3 clinically ranked alternatives.
The LLM only extracts findings into known concepts and phrases text. Exposed as tools to the
companion; no fifth agent. Hypotheses are never shown or stored as conditions.

## Consequences

Safe, explainable and measurable navigation that works without model keys. The knowledge base needs
curation (task D08). Coverage is limited to the condition groups in the graph; the general physician
fallback covers the rest.

## Alternatives considered

LLM-led intake with RAG (unsafe and hard to audit), a separate navigation agent (cost without a
measurable gain), showing hypotheses to doctors (moves toward clinical decision support and device
regulation).
