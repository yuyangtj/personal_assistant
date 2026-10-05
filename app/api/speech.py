"""Speech synthesis for voice clients."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.api.schemas import SpeechRequest
from app.integrations.chat import ProviderError

router = APIRouter()


@router.post("/speech", response_class=Response)
def synthesize_speech(body: SpeechRequest, request: Request) -> Response:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Speech text cannot be empty")
    synthesizer = request.app.state.speech_synthesizer
    if synthesizer is None:
        raise HTTPException(status_code=503, detail="Cloud speech is not configured")
    try:
        result = synthesizer.synthesize(text, emotion=body.emotion)
    except ProviderError as error:
        raise HTTPException(status_code=502, detail="Cloud speech generation failed") from error
    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Speech-Provider": result.provider,
            "X-Speech-Model": result.model,
            "X-Speech-Voice": result.voice,
            "X-Speech-Duration-Ms": str(result.duration_ms),
            "X-Speech-Cache": "hit" if result.cache_hit else "miss",
        },
    )
