"""Notification routes (spec 10): my notifications, mark read, and `/me/stream`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.responses import StreamingResponse

from suwapath.contracts.common import Page
from suwapath.contracts.errors import NotFound
from suwapath.contracts.ids import ResourceId, UserId
from suwapath.contracts.ports.notifications import Notification
from suwapath.contracts.principal import Principal
from suwapath.modules.notifications.application.service import SqlNotificationService, stream_key
from suwapath.modules.notifications.application.stream import my_events
from suwapath.platform.http.auth import require, require_stream
from suwapath.platform.http.stubs import ALL_ROLES

Anyone = Annotated[Principal, Depends(require(*ALL_ROLES))]
StreamOwner = Annotated[
    Principal, Depends(require_stream(*ALL_ROLES, stream_key=lambda request: "me"))
]


def build_router(service: SqlNotificationService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["notifications"])

    @router.get(
        "/notifications",
        summary="My notifications",
        operation_id="notifications_get_notifications",
    )
    async def mine(
        principal: Anyone,
        cursor: Annotated[str | None, Query(max_length=64)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> Page[Notification]:
        return await service.list(principal, cursor, limit)

    @router.post(
        "/notifications/read-all",
        summary="Mark all read",
        operation_id="notifications_post_notifications_read_all",
        status_code=204,
    )
    async def read_all(principal: Anyone) -> Response:
        await service.mark_read(principal, None)
        return Response(status_code=204)

    @router.post(
        "/notifications/{notification_id}/read",
        summary="Mark read",
        operation_id="notifications_post_notifications_notification_id_read",
        status_code=204,
    )
    async def read_one(
        notification_id: Annotated[ResourceId, Path()], principal: Anyone
    ) -> Response:
        await service.mark_read(principal, notification_id)
        return Response(status_code=204)

    @router.get(
        "/me/stream",
        summary="My events (SSE)",
        operation_id="notifications_get_me_stream",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def stream(
        principal: StreamOwner,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=40)] = None,
    ) -> StreamingResponse:
        hub = getattr(request.app.state, "stream_hub", None)
        if hub is None:
            raise NotFound()
        assert principal.user_id is not None
        user = UserId(principal.user_id)
        subscription = await hub.subscribe(stream_key(user), user)
        events = my_events(service, user, subscription, last_event_id, request.is_disconnected)
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
