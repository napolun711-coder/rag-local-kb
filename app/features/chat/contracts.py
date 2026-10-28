"""Chat request contracts compatible with the existing web client."""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    session_id: str = Field(default="", max_length=200)
    client_message_id: str = Field(default="", max_length=200)
    topic_id: str = Field(default="", max_length=200)
    force_bypass: bool = False
    force_upstream: bool = False


class SessionCreateRequest(BaseModel):
    title: str = Field(default="", max_length=120)
    topic_id: str = Field(default="", max_length=200)


class SessionPatchRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    topic_id: Optional[str] = Field(default=None, max_length=200)
    status: Optional[str] = None


class SessionImportRequest(BaseModel):
    sessions: List[dict[str, Any]] = Field(default_factory=list)


class HistorySearchItem(BaseModel):
    message_id: str
    session_id: str
    session_title: str = ""
    topic_id: Optional[str] = None
    topic_name: Optional[str] = None
    role: Literal["user", "assistant"]
    snippet: str
    created_at: str


class HistorySearchResponse(BaseModel):
    enabled: bool
    count: int = 0
    items: List[HistorySearchItem] = Field(default_factory=list)


__all__ = [
    "ChatRequest",
    "HistorySearchItem",
    "HistorySearchResponse",
    "SessionCreateRequest",
    "SessionImportRequest",
    "SessionPatchRequest",
]
