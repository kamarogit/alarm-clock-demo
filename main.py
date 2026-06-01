import os
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from database import init_db, get_db
from models import Alarm, AlarmRun, GeneratedMessage
from scheduler import scheduler, setup_scheduler, register_alarm, unregister_alarm, snooze_alarm, set_manager, register_briefing, unregister_briefing

ROOT_PATH = os.getenv("ROOT_PATH", "").rstrip("/")


# ── WebSocket 接続管理 ────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, device_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[device_id] = ws

    def disconnect(self, device_id: str):
        self._connections.pop(device_id, None)

    async def broadcast(self, data: dict):
        for device_id, ws in list(self._connections.items()):
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(device_id)

    @property
    def connected_count(self):
        return len(self._connections)

manager = ConnectionManager()
set_manager(manager)


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await setup_scheduler()
    scheduler.start()
    # Whisper・ウェイクワードモデルをバックグラウンドでプリロード
    import asyncio
    from whisper_stt import preload as whisper_preload
    from wakeword import preload as wakeword_preload
    asyncio.create_task(whisper_preload())
    asyncio.create_task(wakeword_preload())
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan, title="AlarmClock", root_path=ROOT_PATH)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── PWA ───────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request, "base_url": ROOT_PATH})


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/{device_id}")
async def websocket_endpoint(websocket: WebSocket, device_id: str):
    await manager.connect(device_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            event = data.get("type")

            if event == "acknowledged":
                run_id = data.get("run_id")
                async with AsyncSessionLocal() as db:
                    run = await db.get(AlarmRun, run_id)
                    if run and run.status == "ringing":
                        run.status = "acknowledged"
                        run.acknowledged_at = datetime.utcnow()
                        await db.commit()

            elif event == "snooze":
                run_id = data.get("run_id")
                minutes = data.get("minutes", 10)
                await snooze_alarm(run_id, minutes)

    except WebSocketDisconnect:
        manager.disconnect(device_id)


# ── Pydantic models ───────────────────────────────────────────────────────────

class AlarmCreate(BaseModel):
    name: str
    wake_time: str          # "HH:MM"
    repeat_rule: str = "daily"
    enabled: bool = True
    strictness_level: int = 2
    voice_style: str = "friendly"
    sound_type: str = "beep"

class AlarmUpdate(BaseModel):
    name: Optional[str] = None
    wake_time: Optional[str] = None
    repeat_rule: Optional[str] = None
    enabled: Optional[bool] = None
    strictness_level: Optional[int] = None
    voice_style: Optional[str] = None
    sound_type: Optional[str] = None


def _alarm_dict(a: Alarm) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "wake_time": a.wake_time,
        "repeat_rule": a.repeat_rule,
        "enabled": a.enabled,
        "strictness_level": a.strictness_level,
        "voice_style": a.voice_style,
        "sound_type": a.sound_type or "beep",
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ── API: アラーム CRUD ────────────────────────────────────────────────────────

@app.get("/api/alarms")
async def list_alarms(db: AsyncSession = Depends(get_db)):
    alarms = (await db.execute(select(Alarm).order_by(Alarm.wake_time))).scalars().all()
    return [_alarm_dict(a) for a in alarms]


@app.post("/api/alarms", status_code=201)
async def create_alarm(body: AlarmCreate, db: AsyncSession = Depends(get_db)):
    alarm = Alarm(**body.model_dump())
    db.add(alarm)
    await db.commit()
    await db.refresh(alarm)
    if alarm.enabled:
        register_alarm(alarm)
    return _alarm_dict(alarm)


@app.patch("/api/alarms/{alarm_id}")
async def update_alarm(alarm_id: int, body: AlarmUpdate, db: AsyncSession = Depends(get_db)):
    alarm = await db.get(Alarm, alarm_id)
    if not alarm:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(alarm, k, v)
    alarm.updated_at = datetime.utcnow()
    await db.commit()
    unregister_alarm(alarm_id)
    if alarm.enabled:
        register_alarm(alarm)
    return _alarm_dict(alarm)


@app.delete("/api/alarms/{alarm_id}", status_code=204)
async def delete_alarm(alarm_id: int, db: AsyncSession = Depends(get_db)):
    alarm = await db.get(Alarm, alarm_id)
    if not alarm:
        raise HTTPException(404)
    unregister_alarm(alarm_id)
    await db.delete(alarm)
    await db.commit()


# ── API: 次回アラーム ──────────────────────────────────────────────────────────

@app.get("/api/alarms/next")
async def next_alarm(db: AsyncSession = Depends(get_db)):
    now = datetime.now()
    alarms = (await db.execute(
        select(Alarm).where(Alarm.enabled == True).order_by(Alarm.wake_time)
    )).scalars().all()

    next_dt = None
    next_alarm_obj = None
    for alarm in alarms:
        h, m = map(int, alarm.wake_time.split(":"))
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        # weekday チェック
        if alarm.repeat_rule == "weekday":
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
        elif alarm.repeat_rule == "weekend":
            while candidate.weekday() < 5:
                candidate += timedelta(days=1)
        if next_dt is None or candidate < next_dt:
            next_dt = candidate
            next_alarm_obj = alarm

    if not next_alarm_obj:
        return {"next": None}
    return {
        "next": {
            "alarm_id": next_alarm_obj.id,
            "name": next_alarm_obj.name,
            "wake_time": next_alarm_obj.wake_time,
            "datetime": next_dt.isoformat(),
        }
    }


# ── API: 起床確認・スヌーズ ────────────────────────────────────────────────────

@app.post("/api/alarm-runs/{run_id}/ack")
async def ack_alarm(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.get(AlarmRun, run_id)
    if not run:
        raise HTTPException(404)
    run.status = "acknowledged"
    run.acknowledged_at = datetime.utcnow()
    await db.commit()
    return {"status": "acknowledged"}


@app.post("/api/alarm-runs/{run_id}/snooze")
async def snooze(run_id: int, minutes: int = 10, db: AsyncSession = Depends(get_db)):
    run = await db.get(AlarmRun, run_id)
    if not run:
        raise HTTPException(404)
    await snooze_alarm(run_id, minutes)
    return {"status": "snoozed", "minutes": minutes}


# ── API: ブリーフィング設定 ────────────────────────────────────────────────────

from briefing import load_config as _load_briefing_config, save_config as _save_briefing_config

class BriefingUpdate(BaseModel):
    enabled: Optional[bool] = None
    time: Optional[str] = None

@app.get("/api/briefing")
async def get_briefing():
    return _load_briefing_config()

@app.patch("/api/briefing")
async def update_briefing(body: BriefingUpdate):
    config = _load_briefing_config()
    if body.enabled is not None:
        config["enabled"] = body.enabled
    if body.time is not None:
        config["time"] = body.time
    _save_briefing_config(config)
    unregister_briefing()
    if config.get("enabled", True):
        register_briefing(config["time"])
    return config

class BriefingTestBody(BaseModel):
    text: Optional[str] = None

@app.post("/api/briefing/test")
async def test_briefing(body: BriefingTestBody = BriefingTestBody()):
    """ブリーフィングを即時生成・配信（動作確認用）。textを指定するとLLMをスキップ。"""
    import asyncio
    if body.text:
        async def _fire_custom(text: str):
            from briefing import generate_audio
            try:
                audio_url = await generate_audio(text)
            except Exception:
                audio_url = None
            await manager.broadcast({"type": "morning_briefing", "text": text, "audio_url": audio_url})
        asyncio.create_task(_fire_custom(body.text))
    else:
        from scheduler import fire_briefing
        asyncio.create_task(fire_briefing())
    return {"status": "generating"}


# ── API: 音声アシスタント ──────────────────────────────────────────────────────

class VoiceAskBody(BaseModel):
    question: str

@app.post("/api/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)):
    import logging
    logger = logging.getLogger(__name__)
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


# ── API: テスト発火 ────────────────────────────────────────────────────────────

@app.post("/api/test/fire")
async def test_fire(message: str = "テスト: おはようございます。"):
    """動作確認用。WebSocket 接続中のクライアントに即座にアラームを送る。"""
    await manager.broadcast({"type": "alarm_triggered", "run_id": 0, "message": message})
    return {"status": "fired", "clients": manager.connected_count}


# ── API: サウンド一覧 ──────────────────────────────────────────────────────────

BUILTIN_SOUNDS = ["beep", "double", "rising", "gentle"]
SOUNDS_DIR = os.path.join(os.path.dirname(__file__), "static", "sounds")
SOUND_EXTENSIONS = {".mp3", ".ogg", ".wav"}

@app.get("/api/sounds")
async def list_sounds():
    files = []
    if os.path.isdir(SOUNDS_DIR):
        for f in sorted(os.listdir(SOUNDS_DIR)):
            if os.path.splitext(f)[1].lower() in SOUND_EXTENSIONS:
                files.append(f)
    return {"builtin": BUILTIN_SOUNDS, "files": files}


# ── API: ステータス ────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    return {
        "scheduler_running": scheduler.running,
        "connected_clients": manager.connected_count,
        "jobs": [{"id": j.id, "next_run": str(j.next_run_time)} for j in scheduler.get_jobs()],
    }


# DB セッションをスケジューラー内で使うため
from database import AsyncSessionLocal
