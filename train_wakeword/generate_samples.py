"""
ウェイクワードの学習用音声サンプルを生成する
edge-tts で「クロード」「ねえクロード」を多数生成する

実行: python generate_samples.py
"""

import asyncio
import os
import random
import numpy as np
import soundfile as sf
from pathlib import Path
import edge_tts

# ── 設定 ────────────────────────────────────────────────────────────────────

POSITIVE_DIR = Path("data/positive")   # ウェイクワードのサンプル
NEGATIVE_DIR = Path("data/negative")   # 非ウェイクワードのサンプル

# ウェイクワードのバリエーション
WAKE_PHRASES = [
    "ねえクロード",
    "ねークロード",
    "ねぇクロード",
    "クロード",
    "ねえ、クロード",
]

# ネガティブサンプル（ウェイクワードに似ているが違うもの）
NEGATIVE_PHRASES = [
    "おはようございます",
    "今日の天気は",
    "ありがとうございます",
    "電車が来ます",
    "時間を教えて",
    "明日の予定は",
    "音楽をかけて",
    "アラームを止めて",
    "ニュースを読んで",
    "いい天気ですね",
    "お疲れ様です",
    "よろしくお願いします",
    "大丈夫ですか",
    "行ってきます",
    "ただいまです",
    "こんにちは",
    "こんばんは",
    "おやすみなさい",
    "はじめまして",
    "すみません",
]

# 日本語の音声一覧（edge-ttsで確認済み）
JP_VOICES = [
    "ja-JP-NanamiNeural",
    "ja-JP-KeitaNeural",
]


async def generate_tts(text: str, voice: str, rate: str, output_path: Path) -> bool:
    for attempt in range(3):
        try:
            await asyncio.sleep(0.8)  # レート制限対策
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(output_path))
            return True
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2)
            else:
                print(f"  SKIP: {text} / {voice}: {e}")
    return False


async def generate_all():
    POSITIVE_DIR.mkdir(parents=True, exist_ok=True)
    NEGATIVE_DIR.mkdir(parents=True, exist_ok=True)

    # ── ポジティブサンプル生成 ─────────────────────────────────────────────
    print("=== ポジティブサンプル生成中 ===")
    rates = ["-10%", "+0%", "+10%", "+20%", "-20%"]
    count = 0
    for phrase in WAKE_PHRASES:
        for voice in JP_VOICES:
            for rate in rates:
                path = POSITIVE_DIR / f"pos_{count:04d}.mp3"
                ok = await generate_tts(phrase, voice, rate, path)
                if ok:
                    count += 1
                    print(f"  [{count}] {phrase} / {voice} / {rate}")
    print(f"ポジティブ: {count}件生成")

    # ── ネガティブサンプル生成 ─────────────────────────────────────────────
    print("\n=== ネガティブサンプル生成中 ===")
    count = 0
    for phrase in NEGATIVE_PHRASES:
        for voice in JP_VOICES:
            for rate in ["+0%", "+10%"]:
                path = NEGATIVE_DIR / f"neg_{count:04d}.mp3"
                ok = await generate_tts(phrase, voice, rate, path)
                if ok:
                    count += 1
                    print(f"  [{count}] {phrase} / {voice}")

    # ── 無音・ノイズサンプル追加 ───────────────────────────────────────────
    print("\n=== 無音・ノイズサンプル生成中 ===")
    sr = 16000
    for i in range(50):
        # 白色雑音
        noise = (np.random.randn(sr * 2) * 0.05).astype(np.float32)
        sf.write(str(NEGATIVE_DIR / f"noise_{i:03d}.wav"), noise, sr)
    count += 50
    print(f"ネガティブ: {count}件生成")

    print("\n完了！ data/ フォルダを確認してください。")


if __name__ == "__main__":
    asyncio.run(generate_all())
