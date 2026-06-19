# backend/ws_manager.py
"""
WebSocket Connection Manager for MPDPMS v4.
Manages per-project WebSocket connections for:
  - simulation_progress: {task_id, pct, eta_s}
  - simulation_done:     {task_id, results_url}
  - anomaly_alert:       {tag, value, sigma, recommendation}
  - live_tag:            {tag_id, value, timestamp}
  - collab_presence:     {user_id, module, action}
"""
from __future__ import annotations
import logging
from collections import defaultdict
from typing import Dict, List
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # project_id → list of connected WebSocket clients
        self.active_connections: Dict[str, List[WebSocket]] = defaultdict(list)

    async def connect(self, project_id: str, websocket: WebSocket):
        try:
            await websocket.accept()
            self.active_connections[project_id].append(websocket)
            logger.debug("WS connected: project=%s total=%d",
                         project_id, len(self.active_connections[project_id]))
        except Exception as e:
            logger.error("WebSocket connection failed for project=%s: %s", project_id, e)

    def disconnect(self, project_id: str, websocket: WebSocket):
        conns = self.active_connections.get(project_id, [])
        if websocket in conns:
            conns.remove(websocket)
        logger.debug("WS disconnected: project=%s total=%d", project_id, len(conns))

    async def broadcast(self, project_id: str, message: dict):
        """Send message to all clients subscribed to this project."""
        dead = []
        for ws in list(self.active_connections.get(project_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(project_id, ws)

    async def send_to(self, project_id: str, websocket: WebSocket, message: dict):
        """Send message to a single client."""
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(project_id, websocket)

    async def broadcast_progress(
        self, project_id: str, task_id: str, pct: int, step: str, eta_s: int | None = None,
    ):
        """Broadcast task progress to all project clients."""
        try:
            await self.broadcast(project_id, {
                "type": "task_progress",
                "task_id": task_id,
                "pct": pct,
                "step": step,
                "eta_s": eta_s,
            })
        except Exception as e:
            logger.error("Failed to broadcast progress for task=%s project=%s: %s", task_id, project_id, e)

    async def broadcast_task_done(
        self, project_id: str, task_id: str, status: str = "done", error: str | None = None,
    ):
        """Broadcast task completion/failure."""
        try:
            await self.broadcast(project_id, {
                "type": "task_done",
                "task_id": task_id,
                "status": status,
                "error": error,
            })
        except Exception as e:
            logger.error("Failed to broadcast task_done for task=%s project=%s: %s", task_id, project_id, e)


# Singleton instance shared across the FastAPI app
ws_manager = ConnectionManager()
