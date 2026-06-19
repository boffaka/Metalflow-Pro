"""MetalFlow Pro — AI Assistant API route."""
from __future__ import annotations

import logging
import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user
    from ..db import qone, qall
    from ..engines.assistant import chat
except ImportError:
    from auth import project_user
    from db import qone, qall
    from engines.assistant import chat

router = APIRouter(tags=["assistant"])


class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


@router.post("/{pid}/assistant/chat")
def assistant_chat(pid: str, body: ChatMessage, user=Depends(project_user)):
    """Send a message to the AI metallurgical assistant."""
    try:
        result = chat(pid, body.message, qone, qall)
        return {
            "response": result["response"],
            "source": result["source"],
            "intent": result["intent"],
            "citations": result.get("citations", []),
            "suggested_actions": result.get("suggested_actions", []),
            "limitations": result.get("limitations"),
            "user": user.get("full_name") or user.get("email"),
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
