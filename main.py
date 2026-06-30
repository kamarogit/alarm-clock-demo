import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

ROOT_PATH = os.getenv("ROOT_PATH", "").rstrip("/")


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Whisper・ウェイクワードモデルをバックグラウンドでプリロード
    import asyncio
    from whisper_stt import preload as whisper_preload
    from wakeword import preload as wakeword_preload
    asyncio.create_task(whisper_preload())
    asyncio.create_task(wakeword_preload())
    yield


app = FastAPI(lifespan=lifespan, title="VoiceAssistant", root_path=ROOT_PATH)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── PWA ───────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request, "base_url": ROOT_PATH})


# ── API: 音声アシスタント ──────────────────────────────────────────────────────

@app.post("/api/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)):
    from whisper_stt import transcribe
    audio_bytes = await audio.read()
    text = await transcribe(audio_bytes)
    print(f"[Transcribe] 結果: {text!r} (blob: {len(audio_bytes)} bytes)", flush=True)
    return {"text": text}


@app.post("/api/voice/wakeword")
async def voice_wakeword(audio: UploadFile = File(...)):
    """カスタムモデルでウェイクワード検出（Whisper不使用・低CPU）"""
    from wakeword import detect
    audio_bytes = await audio.read()
    detected = await detect(audio_bytes)
    print(f"[WakeWord] detected={detected} (blob: {len(audio_bytes)} bytes)", flush=True)
    return {"detected": detected}


class VoiceAskBody(BaseModel):
    question: str


@app.post("/api/voice/ask")
async def voice_ask(body: VoiceAskBody):
    print(f"[Ask] 質問: {body.question!r}", flush=True)
    from voice import answer_question, generate_voice_response
    answer, end_session = await answer_question(body.question)
    audio_url = await generate_voice_response(answer)
    return {"answer": answer, "audio_url": audio_url, "end_session": end_session}


@app.post("/api/voice/session/clear")
async def voice_session_clear():
    """セッション開始・終了時に履歴をリセット"""
    from voice import clear_history
    clear_history()
    return {"status": "cleared"}
