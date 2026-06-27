"""WebSocket authentication, authorization, and connection lifecycle.

Extracted from main.py to keep the main module focused on route registration.
"""
from __future__ import annotations

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState


async def ws_authenticate(websocket: WebSocket, token: str | None) -> dict | None:
    """Validate JWT from WebSocket query string.

    Closes connection with code 4401 if the token is missing or invalid.
    Returns the user row on success, ``None`` on failure (after closing the socket).
    """
    if not token:
        await websocket.close(code=4401, reason="missing_token")
        return None
    try:
        try:
            from .auth import resolve_current_user
        except ImportError:
            from auth import resolve_current_user
        from fastapi.security import HTTPAuthorizationCredentials
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        user = resolve_current_user(None, creds)
        return user
    except Exception:
        await websocket.close(code=4401, reason="invalid_token")
        return None


async def ws_check_project(websocket: WebSocket, project_id: str, user: dict | None = None) -> bool:
    """Verify the project exists and the user may access it.

    Closes the socket with 4404 if the project is not found or access is denied,
    or 1011 on server error.
    """
    try:
        try:
            from .auth import ensure_project_access
        except ImportError:
            from auth import ensure_project_access
        if user:
            ensure_project_access(project_id, user)
            return True
        try:
            from .db import qone as _qone
        except ImportError:
            from db import qone as _qone
        proj = _qone("SELECT id FROM projects WHERE id=%s", (project_id,))
    except HTTPException:
        await websocket.close(code=4404, reason="project_not_found")
        return False
    except Exception:
        await websocket.close(code=1011, reason="server_error")
        return False
    if not proj:
        await websocket.close(code=4404, reason="project_not_found")
        return False
    return True


async def ws_run_connection(
    ws_manager,
    channel: str,
    websocket: WebSocket,
    token: str | None,
    project_id: str,
) -> None:
    """Full WebSocket lifecycle: authenticate, check project, connect, message loop."""
    user = await ws_authenticate(websocket, token)
    if not user:
        return
    if not await ws_check_project(websocket, project_id, user):
        return
    if websocket.client_state != WebSocketState.CONNECTED:
        return
    await ws_manager.connect(channel, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)


async def ws_run_live_connection(
    ws_manager,
    channel: str,
    websocket: WebSocket,
    token: str | None,
    project_id: str,
) -> None:
    """Full WebSocket lifecycle for live-tag streams (keep-alive only)."""
    user = await ws_authenticate(websocket, token)
    if not user:
        return
    if not await ws_check_project(websocket, project_id, user):
        return
    if websocket.client_state != WebSocketState.CONNECTED:
        return
    await ws_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)
