"""TTS status and synthesis API."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.features.tts import service
from app.features.access.service import is_local_request

router = APIRouter(prefix="/api/tts", tags=["tts"])


class SpeechRequest(BaseModel):
    text: str = Field(default="", max_length=2000)


def _require_local(request: Request) -> None:
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="This API is local-only")


@router.get("/status")
def tts_status() -> Dict[str, Any]:
    return service.status()


@router.post("/test", dependencies=[Depends(_require_local)])
def test_tts() -> Dict[str, Any]:
    return service.test_connection()


@router.post("")
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
