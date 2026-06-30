"""
音声文字起こし

STT_BACKEND 環境変数で切り替え:
  openai : OpenAI Whisper API（whisper-1）
  groq   : Groq Whisper API（whisper-large-v3・無料枠あり）
  local  : faster-whisper ローカル（GPU環境向け）
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_STT_BACKEND = os.getenv("STT_BACKEND", "openai").lower()

# ── OpenAI / Groq 共通（互換API） ─────────────────────────────────────────────

_STT_CONFIG = {
    "openai": {
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "key_env": "OPENAI_API_KEY",
        "model": "whisper-1",
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "key_env": "GROQ_API_KEY",
        "model": "whisper-large-v3",
    },
}


async def _transcribe_api(audio_bytes: bytes) -> str:
    import httpx
    config = _STT_CONFIG[_STT_BACKEND]
    api_key = os.getenv(config["key_env"], "")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            config["url"],
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.webm", audio_bytes, "audio/webm")},
            data={
                "model": config["model"],
                "language": "ja",
                "prompt": "ねえクロード、今日の天気は？おはよう。",
            },
        )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
    logger.info("[STT] %s認識: %s", _STT_BACKEND, text or "(空)")
    return text


# ── ローカル faster-whisper ───────────────────────────────────────────────────

_model = None
_semaphore = asyncio.Semaphore(1)


def _load_model():
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel
    logger.info("[STT] モデル読み込み中 (small)...")
    _model = WhisperModel("small", device="cpu", compute_type="int8")
    logger.info("[STT] モデル準備完了")
    return _model


def _transcribe_local_sync(audio_bytes: bytes) -> str:
    import io
    model = _load_model()
    audio_io = io.BytesIO(audio_bytes)
    segments, _ = model.transcribe(
        audio_io,
        language="ja",
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        initial_prompt="ねえクロード、今日の天気は？おはよう。",
    )
    text = "".join(s.text for s in segments).strip()
    logger.info("[STT] local認識: %s", text or "(空)")
    return text


async def _transcribe_local(audio_bytes: bytes) -> str:
    async with _semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _transcribe_local_sync, audio_bytes)


# ── 共通インターフェース ───────────────────────────────────────────────────────

async def transcribe(audio_bytes: bytes) -> str:
    if _STT_BACKEND in ("openai", "groq"):
        return await _transcribe_api(audio_bytes)
    return await _transcribe_local(audio_bytes)


async def preload():
    """サービス起動時にバックグラウンドでモデルをロード（localのみ）"""
    if _STT_BACKEND == "local":
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _load_model)
