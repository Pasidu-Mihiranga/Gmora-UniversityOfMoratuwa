# 09. Imaging and lab reports

## 1. Chest X-ray screening (module `imaging`)

`IMG-1` Image models implement `ImagingModel`:

```python
class ImagingModel(Protocol):
    manifest: ModelManifest
    def available(self) -> bool: ...                       # weights present and checksum valid
    async def predict(self, image: PreparedImage) -> ScreeningOutput: ...
```

`IMG-2` Models are registered from manifests in `project/models/<family>/<variant>/manifest.json`.
MVP family `pneumonia` has two variants: `general` (adults) and `paediatric` (children).
The registry picks the variant by the patient's age at the study date:
`age < PAEDIATRIC_MAX_AGE` -> `paediatric`, else `general`. `PAEDIATRIC_MAX_AGE` is taken from the
paediatric model card (the age range of its training data) and is required in its manifest.

### Manifest

```json
{
  "family": "pneumonia",
  "variant": "general",
  "model_version": "resnet50-general-1",
  "weights_file": "general.onnx",
  "weights_sha256": "<hex>",
  "weights_url": "<GitHub release asset URL>",
  "architecture": "ResNet50",
  "input": { "name": "input", "shape": [1, 3, 224, 224], "layout": "NCHW", "color": "RGB",
             "resize": 224, "normalize": { "mean": [], "std": [] } },
  "output": { "name": "output", "kind": "logits", "class_order": ["normal", "pneumonia"] },
  "age_range_years": [null, null],
  "validation_status": "unvalidated",
  "threshold": null,
  "threshold_policy": "chosen on the validation split for sensitivity >= target",
  "metrics": null,
  "training_data": null,
  "model_card": "MODEL_CARD.md"
}
```

`IMG-3` Facts read from the ONNX files (27 Nov 2026): input `input` float `[batch, 3, 224, 224]`,
output `output` `[batch, 2]` raw logits (softmax applied by our code). Class order and normalization
MUST be confirmed from the training code before the manifest is marked complete.

`IMG-4` Every manifest starts with `validation_status: "unvalidated"`, `threshold: null`,
`metrics: null`. It becomes `validated` only when metrics measured on a held-out test set (or
reported by the training run with the test-set description) are written into the manifest by
`python main.py eval --suite imaging`. **No document, slide or UI may state a sensitivity or
specificity that is not in a manifest.**

`IMG-5` While `threshold` is null, the adapter uses 0.5 on the pneumonia probability and the
manifest stays `unvalidated`.

`IMG-6` If weights are missing or the checksum fails, the model is **unavailable**: the upload is
still stored and attached to the record, the result is `screening_unavailable`, and the doctor is
told to review the image directly. There is no fake image model.

### Flow

1. Patient (or doctor) uploads a PNG or JPEG chest X-ray (max 10 MB, min 256 px on the short side).
2. Optional image gate: if the decision provider is configured, Jev answers
   `is_chest_radiograph` (noul). Below 0.5 the result is `not_a_chest_xray` and no screening runs.
   Without Jev the gate is skipped and the result records `gate: "not_run"`.
3. Preprocess exactly as the manifest says. Run the selected variant.
4. Store `ImagingResult(study_id, model_version, validation_status, prob_pneumonia, flagged,
   threshold_used, gate)`. Emit `imaging.screening_completed`.

`IMG-7` `flagged=true` raises the case urgency floor to at least `urgent_24h` and adds an imaging line
to the doctor brief: "Screening model flagged possible pneumonia (model X, unvalidated or validated,
probability P). Please review the image."

`IMG-8` The patient sees only: "Your X-ray has been sent to Dr. X for review." Never a probability,
never a finding.

## 2. Lab reports (module `reports`)

`REP-1` Upload PDF, PNG or JPEG (max 10 MB). Stored encrypted (`SEC-26`). `reports.report_uploaded`.

`REP-2` Extraction pipeline:
1. PDF with a text layer: extract text with PyMuPDF (page and line positions kept).
2. Image or scanned PDF: if the chat provider has vision, extract with a structured-output call
   (`ExtractedReport` schema); else Tesseract OCR if installed; else mark `needs_manual_entry`.
3. Parse rows into `Observation` candidates: test name, value, unit, reference range printed on the
   report, flag (`low`, `normal`, `high`). Test names map to a code table (LOINC where known) for
   the 30 most common tests (HbA1c, fasting glucose, lipid panel, creatinine, eGFR, electrolytes,
   full blood count, liver enzymes, TSH, urine albumin).
4. Each Observation gets a `Provenance` with document ID, page and line.

`REP-3` Explanation: the companion drafts a plain-language explanation per abnormal value ("Your
HbA1c is above the range printed on your report. Please show this to your doctor."), in the
patient's language. It passes the verifier (`AGT-10`). It never interprets a result as a diagnosis
and always ends with a navigation step.

`REP-4` The reference range printed on the report wins over any default range.
