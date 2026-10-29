"""TTS status and synthesis API."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.features.tts import service
from app.features.access.dependencies import require_authorized, require_local

router = APIRouter(prefix="/api/tts", tags=["tts"])


class SpeechRequest(BaseModel):
    text: str = Field(default="", max_length=2000)


@router.get("/status", dependencies=[Depends(require_authorized)])
def tts_status() -> Dict[str, Any]:
    return service.status()


@router.post("/test", dependencies=[Depends(require_local)])
def test_tts() -> Dict[str, Any]:
    return service.test_connection()


@router.post("", dependencies=[Depends(require_authorized)])
def speech(data: SpeechRequest) -> Response:
    try:
        audio = service.synthesize(data.text)
    except service.TtsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="speech.mp3"',
            "X-TTS-Provider": audio.provider,
        },
    )


__all__ = ["router"]
