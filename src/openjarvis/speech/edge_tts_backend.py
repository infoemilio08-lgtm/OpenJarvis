"""Voces neuronales de Microsoft (las de "Leer en voz alta" de Edge).

Gratis y sin clave: usa el paquete ``edge-tts``. Necesita internet.
Devuelve MP3; los navegadores y reproductores lo detectan solos.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import List

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "es-ES-AlvaroNeural"


def _rate(speed: float) -> str:
    pct = int(round((float(speed or 1.0) - 1.0) * 100))
    return f"{pct:+d}%"


@TTSRegistry.register("edge")
class EdgeTTSBackend(TTSBackend):
    """Microsoft Edge neural TTS (free, natural-sounding)."""

    backend_id = "edge"

    def health(self) -> bool:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False
        return True

    def available_voices(self) -> List[str]:
        return [
            "es-ES-AlvaroNeural",
            "es-ES-ElviraNeural",
            "es-ES-XimenaNeural",
            "es-MX-JorgeNeural",
        ]

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "mp3",
    ) -> TTSResult:
        import edge_tts

        voice = voice_id or DEFAULT_VOICE

        async def _run() -> bytes:
            comm = edge_tts.Communicate(text, voice, rate=_rate(speed))
            chunks: list[bytes] = []
            async for part in comm.stream():
                if part.get("type") == "audio":
                    chunks.append(part["data"])
            return b"".join(chunks)

        # Run in a private event loop/thread so this works from sync code and
        # from inside an already-running loop (FastAPI's to_thread workers).
        box: dict = {}

        def _worker() -> None:
            try:
                box["audio"] = asyncio.run(_run())
            except Exception as exc:  # pragma: no cover - network errors
                box["error"] = exc

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=60)
        if "error" in box:
            raise RuntimeError(f"edge-tts failed: {box['error']}") from box["error"]
        audio = box.get("audio") or b""
        if not audio:
            raise RuntimeError("edge-tts returned no audio")
        return TTSResult(audio=audio, format="mp3", voice_id=voice, sample_rate=24000)


__all__ = ["EdgeTTSBackend"]
