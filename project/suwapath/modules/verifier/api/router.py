"""Review routes (spec 10, VER-5): the doctor or the patient sees what waits for them, and sends
or discards it. System admins never review content."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from suwapath.contracts.common import Page
from suwapath.contracts.enums import Purpose, Role
from suwapath.contracts.ids import ResourceId
from suwapath.contracts.ports.verifier import ReviewItem, Verdict
from suwapath.contracts.principal import Principal
from suwapath.modules.verifier.application.service import SqlVerifier
from suwapath.platform.http.auth import require

Reviewer = Annotated[
    Principal,
    Depends(require(Role.PATIENT, Role.DOCTOR, purposes={Role.DOCTOR: Purpose.TREATMENT})),
]


class ReviewDecision(BaseModel):
    decision: Literal["send", "edit", "discard"]
    edited_text: str | None = Field(default=None, max_length=4000)


def build_router(service: SqlVerifier) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["verifier"])

    @router.get(
        "/reviews",
        summary="Messages waiting for my review",
        operation_id="verifier_get_reviews",
    )
    async def reviews(
        principal: Reviewer,
        cursor: Annotated[str | None, Query(max_length=64)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> Page[ReviewItem]:
        return await service.reviews(principal, cursor, limit)

    @router.post(
        "/reviews/{review_id}",
        summary="Send, edit or discard",
        operation_id="verifier_post_reviews_review_id",
    )
    async def decide(
        review_id: Annotated[ResourceId, Path()], body: ReviewDecision, principal: Reviewer
    ) -> Verdict:
        return await service.decide_review(principal, review_id, body.decision, body.edited_text)

    return router
