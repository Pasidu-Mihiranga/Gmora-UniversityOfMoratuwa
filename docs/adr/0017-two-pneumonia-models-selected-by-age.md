# ADR-0017: Two pneumonia models selected by age, unvalidated until measured

Status: Superseded by ADR-0021 (27 Nov 2026)

## Context

We have three trained ResNet50 ONNX models without recorded thresholds or test metrics.

## Decision

Use paediatric for children and general for adults, chosen by age from the paediatric model card. Drop clean. Manifests start unvalidated; metrics only from measured evidence.

## Consequences

Honest claims. Imaging works now and becomes validated when evidence is added.

## Alternatives considered

One model for all ages (worse fit), claiming the proposal's 95% before measuring (unacceptable).
