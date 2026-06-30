"""
音声アシスタント処理

バックエンド（.envのVOICE_BACKENDで切り替え）:
  claude_cli  : claude -p（エージェント・Web検索対応）+ システム管理メモリ
  openrouter  : OpenRouter API（デフォルト）
  ollama      : ローカルOllama
  dify        : オンプレDify

- LLMが[END]タグで会話終了を判断
- edge-tts で音声生成
"""

import asyncio
import os
import re
import time
from pathlib import Path

import httpx
import edge_tts

VOICE_AUDIO_PATH = Path(__file__).parent / "static" / "voice_response.mp3"
TTS_VOICE = "ja-JP-NanamiNeural"
LOG_FILE = Path(__file__).parent / "voice_log.jsonl"
PERSONA_FILE = Path(__file__).parent / "voice_persona.md"


def _load_persona() -> str:
    try:
        return PERSONA_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""

_BACKEND = os.getenv("VOICE_BACKEND", "openrouter").lower()

# openrouter / ollama 用
if _BACKEND == "ollama":
    _API_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/v1/chat/completions"
    _API_KEY = "ollama"
    MODEL = os.getenv("VOICE_MODEL", "llama3")
else:
    _API_URL = "https://openrouter.ai/api/v1/chat/completions"
    _API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    MODEL = os.getenv("VOICE_MODEL", "anthropic/claude-haiku-4-5")

SYSTEM_PROMPT = (
    "あなたは家庭用スマートスピーカーのAIアシスタントです。\n"
    "ユーザーの質問に簡潔な日本語で回答してください。\n"
    "3文以内でまとめ、自然な話し言葉で答えてください。\n"
    "読み上げることを前提に、記号・箇条書き・URL・引用番号・Markdownは絶対に使わず、文章のみで答えてください。\n\n"
    "【重要】会話が自然に終了したと判断した場合（ユーザーが「ありがとう」「じゃあね」"
    "「バイバイ」「終わり」「もういい」などと言った場合）、"
    "回答文の末尾に必ず「[END]」と付けてください。"
    "通常の会話中は絶対に[END]を付けないでください。"
)

# ── セッション状態（RAMのみ・再起動でリセット） ──────────────────────────────
_history: list[dict] = []        # openrouter / ollama / claude_cli 用
_dify_conversation_id: str = ""  # dify 用


def _save_log(history: list[dict]):
    """会話をJSONLファイルに追記（参照用ログ）"""
    if not history:
        return
    import json
    from datetime import datetime
    entry = {"timestamp": datetime.now().isoformat(), "conversation": history}
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def clear_history():
    """セッション開始・終了時に呼ぶ"""
    global _history, _dify_conversation_id
    if _history:
        _save_log(_history)  # ログだけ残す
    _history = []
    _dify_conversation_id = ""


# ── バックエンド別呼び出し ─────────────────────────────────────────────────────

async def _call_claude_cli(question: str) -> str:
    """
    claude -p でエージェント的に回答。
    セッション内会話履歴をプロンプトに埋め込んで文脈を維持する。
    """
    _history.append({"role": "user", "content": question})

    # プロンプト構築（ペルソナ＋セッション中の履歴を全部渡す）
    from datetime import datetime
    import zoneinfo
    now = datetime.now(zoneinfo.ZoneInfo("Asia/Tokyo"))
    persona = _load_persona()
    prompt = SYSTEM_PROMPT
    prompt += f"\n\n【現在日時】{now.strftime('%Y年%m月%d日 %H:%M')}（JST）"
    if persona:
        prompt += f"\n\n【ユーザー情報】\n{persona}"
    if len(_history) > 1:
        prompt += "\n\n【今回の会話履歴】"
        for m in _history[:-1]:
            role = "ユーザー" if m["role"] == "user" else "クロード"
            prompt += f"\n{role}: {m['content']}"
    prompt += f"\n\nユーザー: {question}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt, "--dangerously-skip-permissions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        raw = stdout.decode().strip()
        if raw:
            # レート制限エラーを検出
            if "You've hit your limit" in raw or "hit your limit" in raw.lower():
                reset_hint = ""
                import re as _re
                m = _re.search(r'resets?\s+(\S+)', raw)
                if m:
                    reset_hint = f"リセット時刻は{m.group(1)}です。"
                _history.pop()
                return f"申し訳ありません、ただいまクロードの利用制限に達しています。しばらく経ってからお試しください。{reset_hint}"
            _history.append({"role": "assistant", "content": raw})
            return raw
    except Exception as e:
        print(f"[Voice] claude_cliエラー: {e}", flush=True)
        _history.pop()

    return "すみません、うまく処理できませんでした。もう一度お試しください。"


async def _call_openrouter_ollama(question: str) -> tuple[str, bool]:
    _history.append({"role": "user", "content": question})
    persona = _load_persona()
    system = SYSTEM_PROMPT + (f"\n\n【ユーザー情報】\n{persona}" if persona else "")
    messages = [{"role": "system", "content": system}] + _history
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _API_URL,
                headers={"Authorization": f"Bearer {_API_KEY}"},
                json={"model": MODEL, "max_tokens": 300, "messages": messages},
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[Voice] LLMエラー: {e}", flush=True)
        _history.pop()
        return "すみません、うまく処理できませんでした。もう一度お試しください。", False

    _history.append({"role": "assistant", "content": raw})
    if len(_history) > 20:
        _history[:] = _history[-20:]
    return raw, False


async def _call_dify(question: str) -> tuple[str, bool]:
    """
    Dify Chat API を呼ぶ。
    会話履歴は Dify 側が conversation_id で管理するので _history 不要。
    [END] タグは Dify アプリのシステムプロンプトに追加しておくこと。
    """
    global _dify_conversation_id
    dify_base = os.getenv("DIFY_URL", "").rstrip("/")
    dify_key  = os.getenv("DIFY_API_KEY", "")
    payload = {
        "inputs": {},
        "query": question,
        "response_mode": "blocking",
        "user": "alarm-clock",
        "conversation_id": _dify_conversation_id,
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{dify_base}/v1/chat-messages",
                headers={"Authorization": f"Bearer {dify_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("answer", "").strip()
            _dify_conversation_id = data.get("conversation_id", _dify_conversation_id)
    except Exception as e:
        print(f"[Voice] Difyエラー: {e}", flush=True)
        return "すみません、うまく処理できませんでした。もう一度お試しください。", False

    return raw, False


# ── 共通インターフェース ───────────────────────────────────────────────────────

def _local_answer(question: str) -> str | None:
    """時刻・日付など即答できる質問はLLMをスキップして返す"""
    from datetime import datetime
    import zoneinfo
    now = datetime.now(zoneinfo.ZoneInfo("Asia/Tokyo"))
    q = re.sub(r'[\s、。？?！!　]+', '', question)

    WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
    weekday = WEEKDAYS[now.weekday()]

    if any(k in q for k in ["今何時", "いまなんじ", "今なんじ", "時刻教えて", "時刻は", "何時ですか", "何時かな", "なんじですか", "なんじかな"]):
        return f"今は{now.hour}時{now.minute:02d}分です。"

    if any(k in q for k in ["何曜日", "なんようび", "なんよう"]):
        return f"今日は{now.month}月{now.day}日、{weekday}曜日です。"

    if any(k in q for k in ["今日は何日", "今日の日付", "今日いつ", "何日ですか", "なんにち", "今日なんにち"]):
        return f"今日は{now.year}年{now.month}月{now.day}日、{weekday}曜日です。"

    return None


async def answer_question(question: str) -> tuple[str, bool]:
    """Returns: (answer_text, should_end_session)"""
    local = _local_answer(question)
    if local:
        print(f"[Voice] (local) 回答: {local!r}", flush=True)
        return local, False

    if _BACKEND == "claude_cli":
        raw = await _call_claude_cli(question)
    elif _BACKEND == "dify":
        raw, _ = await _call_dify(question)
    else:
        raw, _ = await _call_openrouter_ollama(question)

    # [END] タグで会話終了を判断（Dify側のプロンプトにも同じ指示を入れること）
    should_end = bool(re.search(r'\[END\]', raw))
    answer = re.sub(r'\s*\[END\]\s*', '', raw).strip()
    answer = _clean_for_tts(answer)

    print(f"[Voice] ({_BACKEND}) 回答: {answer!r} end={should_end}", flush=True)
    return answer, should_end


def _clean_for_tts(text: str) -> str:
    """TTS読み上げ前に不要な記号・引用・URLを除去する"""
    # URLを除去
    text = re.sub(r'https?://\S+', '', text)
    # Markdownリンク [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # 脚注・引用番号 [1] [2] など
    text = re.sub(r'\[\d+\]', '', text)
    # Markdown見出し # ## ###
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 太字・斜体 **text** *text*
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    # 箇条書き記号 - や * の行頭
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    # 連続する空白行を1行に
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


async def generate_voice_response(text: str, root_path: str = "") -> str:
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    await communicate.save(str(VOICE_AUDIO_PATH))
    return f"{root_path}/static/voice_response.mp3?t={int(time.time())}"
