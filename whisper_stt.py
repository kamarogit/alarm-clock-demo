"""
faster-whisper による音声文字起こし

- 初回起動時にモデルをダウンロード（baseモデル約145MB）
- CPU int8量子化で動作
- 同時実行はセマフォで直列化
"""

import asyncio
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

_model = None
_semaphore = asyncio.Semaphore(1)  # 同時1リクエストに制限


def _load_model():
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel
    logger.info("[STT] モデル読み込み中 (small)...")
    _model = WhisperModel("small", device="cpu", compute_type="int8")
    logger.info("[STT] モデル準備完了")
    return _model


def _transcribe_sync(audio_bytes: bytes) -> str:
    import io
    model = _load_model()
    audio_io = io.BytesIO(audio_bytes)
    segments, info = model.transcribe(
        audio_io,
        language="ja",
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        initial_prompt="ねえクロード、今日の天気は？おはよう。",
    )
    text = "".join(s.text for s in segments).strip()
    logger.info("[STT] 認識: %s", text or "(空)")
    return text


async def transcribe(audio_bytes: bytes) -> str:
    async with _semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _transcribe_sync, audio_bytes)


async def preload():
    """サービス起動時にバックグラウンドでモデルをロード"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_model)
