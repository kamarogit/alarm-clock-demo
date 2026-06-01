"""
カスタムウェイクワード検出モジュール
models/wakeword.onnx を使って「クロード」を検出する

使い方:
  from wakeword import detect
  detected = await detect(audio_bytes)  # True/False
"""

import asyncio
import io
import logging
import subprocess
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "models" / "wakeword.onnx"
SR = 16000
DURATION = 2.0
N_MFCC = 40

_session = None
_semaphore = asyncio.Semaphore(1)


def _load_session():
    global _session
    if _session is not None:
        return _session
    if not MODEL_PATH.exists():
        logger.warning("[WakeWord] モデルファイルが見つかりません: %s（スキップ）", MODEL_PATH)
        return None
    try:
        import onnxruntime as ort
        logger.info("[WakeWord] モデル読み込み中...")
        _session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
        logger.info("[WakeWord] モデル準備完了")
    except Exception as e:
        logger.warning("[WakeWord] モデル読み込み失敗: %s（スキップ）", e)
    return _session


def _predict_sync(audio_bytes: bytes) -> bool:
    import librosa

    # WebM → 16kHz mono PCM（ffmpeg経由）
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", "pipe:0", "-ar", str(SR), "-ac", "1",
             "-f", "wav", "pipe:1"],
            input=audio_bytes, capture_output=True, timeout=5
        )
        y, _ = librosa.load(io.BytesIO(proc.stdout), sr=SR, duration=DURATION, mono=True)
    except Exception as e:
        logger.warning("[WakeWord] 音声変換失敗: %s", e)
        return False

    if len(y) < SR * 0.3:
        return False

    # ゼロパディング
    target = int(SR * DURATION)
    if len(y) < target:
        y = np.pad(y, (0, target - len(y)))
    else:
        y = y[:target]

    # MFCC特徴量（モデルの入力サイズ87フレームに揃える）
    N_FRAMES = 87
    mfcc = librosa.feature.mfcc(y=y, sr=SR, n_mfcc=N_MFCC).astype(np.float32)
    if mfcc.shape[1] < N_FRAMES:
        mfcc = np.pad(mfcc, ((0, 0), (0, N_FRAMES - mfcc.shape[1])))
    else:
        mfcc = mfcc[:, :N_FRAMES]
    x = mfcc[np.newaxis, np.newaxis, :, :]  # (1,1,40,87)

    session = _load_session()
    if session is None:
        return False
    logits = session.run(None, {"mfcc": x})[0]  # (1,2)
    prob = float(np.exp(logits[0, 1]) / np.exp(logits[0]).sum())  # softmax

    print(f"[WakeWord] スコア={prob:.3f}", flush=True)
    return prob > 0.92


async def detect(audio_bytes: bytes) -> bool:
    async with _semaphore:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _predict_sync, audio_bytes)


async def preload():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_session)
