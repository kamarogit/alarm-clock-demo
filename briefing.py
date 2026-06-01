"""
朝のブリーフィング生成

- Open-Meteo API（葛飾区の天気、APIキー不要）
- NHK RSS（ニュース見出し）
- Yahoo!路線情報（総武快速線の運行状況）
- claude CLI でブリーフィング文を生成
"""

import asyncio
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

import edge_tts
import httpx

CONFIG_PATH = Path(__file__).parent / "briefing.json"
DEFAULT_CONFIG = {"enabled": True, "time": "07:30"}

AUDIO_PATH = Path(__file__).parent / "static" / "briefing_latest.mp3"
TTS_VOICE = "ja-JP-NanamiNeural"

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_PARAMS = {
    "latitude": 35.7334,   # 葛飾区
    "longitude": 139.8477,
    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
    "timezone": "Asia/Tokyo",
    "forecast_days": 1,
}
NEWS_URL = os.getenv("NEWS_RSS_URL", "https://www3.nhk.or.jp/rss/news/cat0.xml")
NEWS_MAX = 5

TRAIN_URL = "https://transit.yahoo.co.jp/traininfo/area/4/"
TRAIN_UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36"

WMO = {
    0: "快晴", 1: "晴れ", 2: "晴れ時々曇り", 3: "曇り",
    45: "霧", 48: "氷霧",
    51: "小雨", 53: "雨", 55: "強い雨",
    61: "小雨", 63: "雨", 65: "大雨",
    71: "小雪", 73: "雪", 75: "大雪",
    80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    95: "雷雨", 96: "雷雨と雹", 99: "激しい雷雨",
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2))


async def fetch_weather() -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(WEATHER_URL, params=WEATHER_PARAMS)
        data = r.json()
    daily = data["daily"]
    code = int(daily["weather_code"][0])
    tmax = daily["temperature_2m_max"][0]
    tmin = daily["temperature_2m_min"][0]
    rain = daily["precipitation_sum"][0]
    desc = WMO.get(code, "不明")
    return f"{desc}、最高{tmax}度、最低{tmin}度、降水量{rain}mm"


async def fetch_news() -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(NEWS_URL)
    root = ET.fromstring(r.text)
    titles = [item.findtext("title", "") for item in root.findall(".//item")[:NEWS_MAX]]
    return " / ".join(t for t in titles if t)


async def fetch_train_status() -> str:
    """Yahoo!路線情報から総武快速線の運行状況を取得。"""
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": TRAIN_UA}) as client:
            r = await client.get(TRAIN_URL)
        html = r.text

        # HTMLタグを除去するヘルパー
        def strip_tags(s: str) -> str:
            return re.sub(r"<[^>]+>", "", s).strip()

        # 総武快速線の情報ブロックを探す
        # Yahoo!路線情報は <dt> に路線名、<dd> に状態が入る構造
        pattern = re.compile(
            r"(<dt[^>]*>.*?総武.*?快速.*?</dt>.*?<dd[^>]*>.*?</dd>)",
            re.DOTALL | re.IGNORECASE,
        )
        m = pattern.search(html)
        if m:
            block = strip_tags(m.group(1))
            block = re.sub(r"\s+", " ", block).strip()
            # 状態キーワードを判定
            if any(k in block for k in ("遅延", "運転見合わせ", "運休", "大幅")):
                return f"総武快速線: {block[:60]}"
            return "総武快速線: 平常運転"

        # dt/dd が見つからなくても「総武」と「快速」が同一ブロックにあれば拾う
        idx = html.find("総武")
        while idx != -1:
            snippet = html[idx:idx + 300]
            if "快速" in snippet:
                clean = re.sub(r"\s+", " ", strip_tags(snippet)).strip()
                if any(k in clean for k in ("遅延", "運転見合わせ", "運休")):
                    return f"総武快速線: 遅延・乱れあり"
                break
            idx = html.find("総武", idx + 1)

        return "総武快速線: 平常運転"
    except Exception:
        return "総武快速線: 情報取得失敗"


async def generate_briefing() -> str:
    today = datetime.now(JST).strftime("%-m月%-d日")

    weather, news, train = await asyncio.gather(
        fetch_weather(),
        fetch_news(),
        fetch_train_status(),
        return_exceptions=True,
    )

    if isinstance(weather, Exception):
        weather = "取得できませんでした"
    if isinstance(news, Exception):
        news = "取得できませんでした"
    if isinstance(train, Exception):
        train = "取得できませんでした"

    summary = (
        f"日付: {today}\n"
        f"天気（東京都葛飾区）: {weather}\n"
        f"総武快速線運行状況: {train}\n"
        f"ニュース見出し: {news}"
    )
    prompt = (
        "朝のアシスタントとして、以下の情報をもとに自然な日本語で朝のブリーフィングを作成してください。"
        "話しかけるような口調で、4文以内でまとめてください。挨拶から始めてください。"
        "電車が平常運転の場合はその旨も一言触れてください。\n\n"
        + summary
    )

    return await _call_llm(prompt, fallback=f"おはようございます。{today}の朝です。今日も一日よろしくお願いします。")


async def _call_llm(prompt: str, fallback: str = "") -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
        result = stdout.decode().strip()
        if result:
            return result
    except Exception:
        pass
    return fallback


async def generate_audio(text: str) -> str:
    """テキストをMP3に変換して保存。キャッシュ回避用タイムスタンプ付きURLを返す。"""
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(str(AUDIO_PATH))
    return f"/static/briefing_latest.mp3?t={int(time.time())}"
