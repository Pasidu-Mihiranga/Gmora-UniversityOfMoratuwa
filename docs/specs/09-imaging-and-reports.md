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
MVP: one model, `pneumonia/general` (`general.onnx`), for all ages (ADR-0021). The registry still
supports several variants with selection rules, so other models are a manifest away.

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
  "validation_status": "reported",
  "threshold": 0.165,
  "threshold_status": "provisional",
  "uncertain_band": 0.04,
  "hard_floor": 0.5,
  "threshold_policy": "tuned on the dev set for pneumonia recall >= 0.95 (team-reported)",
  "metrics": {
    "source": "PneumoScan final pitch, Team Gmora (team-reported)",
    "test_set": "3,510 held-out images (paediatric 859, adult 2,651), not used for training or threshold tuning",
    "auc_roc": 0.988, "auc_roc_paediatric": 0.995, "auc_roc_adult": 0.983,
    "accuracy": 0.945, "accuracy_threshold": null,
    "sensitivity": null, "specificity": null
  },
  "training_data": "23,405 chest X-rays, adult and paediatric combined, with synthetic phone-capture artefacts (team-reported; dataset names to be added)",
  "model_card": "MODEL_CARD.md"
}
```

`IMG-3` Facts read from the ONNX file (27 Nov 2026): input `input` float `[batch, 3, 224, 224]`,
output `output` `[batch, 2]` raw logits (softmax applied by our code). From the PneumoScan pitch:
ResNet-50 (ImageNet-pretrained, frozen backbone, fine-tuned 2-class head), grayscale converted to
RGB, 224 x 224. Class order (assumed `[normal, pneumonia]`) and normalization MUST be confirmed from
the training code; until then `threshold_status` stays `provisional`.

`IMG-4` `validation_status` has three values:
- `unvalidated`: no metrics;
- `reported`: metrics from the team's own training documentation, with their source recorded;
- `validated`: metrics re-measured by our reproducible evaluation (`python main.py eval --suite
  imaging`) on a held-out set whose provenance is recorded.
**No document, slide or UI may state a metric that is not in a manifest, and must say which status it
has.** No sensitivity is claimed until it is measured.

`IMG-5` Decision bands on P(pneumonia), shown to the doctor only:

| Band | Rule | Doctor sees |
|---|---|---|
| `no_clear_signs` | p < threshold - band | "No clear signs" |
| `uncertain` | threshold - band <= p <= threshold + band | "Uncertain: radiologist review advised" |
| `possible` | threshold + band < p <= 0.5 | "Possible pneumonia" |
| `signs` | p > 0.5 | "Signs of pneumonia" |

Hard invariant: p > 0.5 can never be shown as `no_clear_signs`, whatever the threshold. `flagged` is
true for `uncertain`, `possible` and `signs`.

`IMG-6` If weights are missing or the checksum fails, the model is **unavailable**: the upload is
still stored and attached to the record, the result is `screening_unavailable`, and the doctor is
told to review the image directly. There is no offline stand-in for the image model.

### Flow

1. Patient (or doctor) uploads a PNG or JPEG chest X-ray (max 10 MB, min 256 px on the short side).
2. Optional image gate: if the decision provider is configured, Jev answers
   `is_chest_radiograph` (noul). Below 0.5 the result is `not_a_chest_xray` and no screening runs.
   Without Jev the gate is skipped and the result records `gate: "not_run"`.
3. Preprocess exactly as the manifest says. Run the selected variant.
4. Store `ImagingResult(study_id, model_version, validation_status, prob_pneumonia, flagged,
   threshold_used, gate)`. Emit `imaging.screening_completed`.

`IMG-7` `flagged=true` raises the case urgency floor to at least `urgent_24h` and adds an imaging line
to the doctor brief: "Screening model: <band> (model X, metrics <status>, probability P). Please review the image."

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

## 3. Uploads (reports and images)

`UPL-1` **Capture in the MVP is upload**: PDF, JPEG or PNG from files, or the phone camera through the
browser's file input with `capture="environment"` (opens the camera on mobile, no custom scanner).
An in-app document scanner (edge detection, perspective correction, CLAHE) is P1.

`UPL-2` Validation on upload, before anything is stored:
- type by **magic bytes**, not by file name or the browser's content type; only PDF, JPEG, PNG;
- size at most 10 MB; images at most 4096 px on the long side (larger are downscaled);
- PDFs at most 20 pages; encrypted or malformed PDFs are rejected with a clear message.

`UPL-3` **Content disarm and reconstruction (CDR).** We do not try to detect bad files; we rebuild
every file from its content and keep only the rebuilt copy:
- the file must parse completely as its detected type (a file that is two formats at once is
  rejected);
- images are decoded and **re-encoded** (PNG for X-rays, JPEG for photos), which strips EXIF
  metadata including GPS location and drops anything appended; a pixel limit (40 megapixels) stops
  decompression bombs;
- PDFs are opened with PyMuPDF, **scrubbed** (JavaScript, embedded files, attachments, links, forms,
  annotations and metadata removed) and saved as a new clean file;
- the original upload is discarded after reconstruction.

`UPL-4` The upload request returns `202 Accepted` with the document ID and status `processing`
immediately. Extraction, OCR and imaging inference run in the worker as event subscribers
(`reports.report_uploaded`, `imaging.study_uploaded`), with a 30 s time limit (60 s for OCR), and
the result reaches the browser by the SSE event `document.processed`.
Status: `processing`, `ready`, `failed`, `needs_manual_entry`.

`UPL-5` PDF parsing uses PyMuPDF for text and page rendering only. Embedded JavaScript, attachments
and links are never executed or followed.

`UPL-6` Files are stored encrypted (`SEC-26`) and served back only through authorized endpoints with
`Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`.

`UPL-7` Malware scanning is a `FileScanner` port with a no-op adapter in the MVP. A ClamAV adapter
(P2) runs in the worker; when enabled, files stay `pending_scan` and are not processed until clean.
