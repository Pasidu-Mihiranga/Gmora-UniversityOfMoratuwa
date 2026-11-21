# ADR-0021: One pneumonia model for all ages, with reported metrics

Status: Accepted (27 Nov 2026). Supersedes ADR-0017.

## Context

ADR-0017 planned a paediatric model for children and a general model for adults, before evidence was
available. The team's PneumoScan final pitch (in the earlier prototype folder) reports that the
combined, phone-trained model (`general.onnx`, from `pneumonia_resnet50_best.pth`) reaches AUC 0.988
on 3,510 held-out images, 0.995 on the paediatric subgroup (n=859) and 0.983 on adults (n=2,651),
94.5% accuracy, and that the paediatric-only model was an earlier version with 87.0% accuracy and a
12.2 point overfitting gap.

## Decision

Use `general.onnx` for all ages. Drop `paediatric.onnx` and `clean.onnx`. Record the pitch metrics
with `validation_status: reported` and their source. Keep the team's decision rule (threshold 0.165,
uncertainty band 0.04, hard floor 0.5) as `provisional` until class order and normalization are
confirmed from the training code. Claim no sensitivity until our own evaluation measures it.

## Consequences

One model to load and test. Honest labelling everywhere. A `validated` status requires our own
reproducible run on a documented held-out set.

## Alternatives considered

Two models selected by age (worse evidence for the paediatric-only model), claiming the proposal's
95% sensitivity now (not supported by measured test evidence).
