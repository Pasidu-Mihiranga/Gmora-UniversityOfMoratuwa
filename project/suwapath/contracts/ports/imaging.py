"""imaging: model registry and screening (IMG-1 to IMG-8). The patient never sees a probability."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from suwapath.contracts.common import Contract
from suwapath.contracts.enums import DocumentStatus, ImagingBand, ValidationStatus
from suwapath.contracts.ids import DocumentId, PatientId, ResourceId
from suwapath.contracts.principal import Principal


class ModelManifest(Contract):
    family: str
    variant: str
    model_version: str
    weights_file: str
    weights_sha256: str
    input: dict[str, Any]
    output: dict[str, Any]
    validation_status: ValidationStatus
    threshold: float | None
    threshold_status: Literal["provisional", "confirmed"] = "provisional"
    uncertain_band: float = 0.0
    hard_floor: float = 0.5
    metrics: dict[str, Any] | None = None
    training_data: str | None = None


class ScreeningOutput(Contract):
    probability: float
    band: ImagingBand


class ImagingModel(Protocol):
    """Extension point (registry `imaging_models`)."""

    manifest: ModelManifest

    def available(self) -> bool: ...

    async def predict(self, image_png: bytes) -> ScreeningOutput: ...


class DoctorScreeningView(Contract):
    study_id: ResourceId
    status: DocumentStatus
    band: ImagingBand | None
    probability: float | None
    model_version: str | None
    validation_status: ValidationStatus | None
    gate: Literal["passed", "failed", "not_run"]


class PatientScreeningView(Contract):
    """IMG-8: status only."""

    study_id: ResourceId
    status: DocumentStatus
    message_key: Literal["sent_for_review", "processing", "could_not_process"]


class ImagingService(Protocol):
    async def upload(
        self, principal: Principal, patient_id: PatientId, data: bytes, content_type: str
    ) -> ResourceId: ...

    async def doctor_view(
        self, principal: Principal, study_id: ResourceId
    ) -> DoctorScreeningView: ...

    async def patient_view(
        self, principal: Principal, study_id: ResourceId
    ) -> PatientScreeningView: ...

    async def process(self, study_id: ResourceId, document_id: DocumentId) -> None:
        """Worker entry point for imaging.study_uploaded."""
        ...
