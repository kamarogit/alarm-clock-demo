"""
アラームスケジューラー

- 起動時に全有効アラームを APScheduler に登録
- 指定時刻に fire_alarm() を実行
- 5分ごとに未確認チェック → 最大3回再通知 → missed 扱い
- スヌーズ時は 10 分後に再発火
"""

import logging
import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from database import AsyncSessionLocal
from models import Alarm, AlarmRun, GeneratedMessage

if TYPE_CHECKING:
    from main import ConnectionManager

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")
_manager: "ConnectionManager | None" = None

RENOTIFY_INTERVAL_MINUTES = 5
MAX_RENOTIFY = 3

TEMPLATES = [
    "おはようございます。起きる時間です。今日も一日よろしくお願いします。",
    "おはようございます。布団から出る時間ですよ。",
    "起床時刻です。気持ちよく一日をスタートしましょう。",
    "おはようございます。今日も頑張りましょう。",
]

def set_manager(manager: "ConnectionManager") -> None:
    global _manager
    _manager = manager


def _generate_message(alarm: Alarm) -> str:
    return random.choice(TEMPLATES)


async def fire_alarm(alarm_id: int) -> None:
    """アラーム発火。AlarmRun を作成して WebSocket で通知。"""
    async with AsyncSessionLocal() as db:
        alarm = await db.get(Alarm, alarm_id)
        if not alarm or not alarm.enabled:
            return

        now = datetime.utcnow()
        run = AlarmRun(
            alarm_id=alarm_id,
            scheduled_at=now,
            triggered_at=now,
            status="ringing",
        )
        db.add(run)
        await db.flush()

        message = _generate_message(alarm)
        sound_type = alarm.sound_type or "beep"
        db.add(GeneratedMessage(alarm_run_id=run.id, message_text=message, source="template"))
        await db.commit()
        run_id = run.id

    logger.info("[ALARM] 発火 alarm_id=%d run_id=%d", alarm_id, run_id)

    if _manager:
        await _manager.broadcast({"type": "alarm_triggered", "run_id": run_id, "message": message, "sound_type": sound_type})

    # 5分後に未確認チェック
    _schedule_renotify(run_id, attempt=1)


async def check_renotify(run_id: int, attempt: int) -> None:
    """未確認なら再通知。MAX_RENOTIFY 回超えたら missed にする。"""
    async with AsyncSessionLocal() as db:
        run = await db.get(AlarmRun, run_id)
        if not run or run.status in ("acknowledged", "missed"):
            return

        if attempt > MAX_RENOTIFY:
            run.status = "missed"
            await db.commit()
            logger.info("[ALARM] missed run_id=%d", run_id)
            return

        run.renotify_count = attempt
        msg_row = (await db.execute(
            select(GeneratedMessage).where(GeneratedMessage.alarm_run_id == run_id)
        )).scalar_one_or_none()
        message = msg_row.message_text if msg_row else TEMPLATES[0]
        alarm = await db.get(Alarm, run.alarm_id)
        sound_type = (alarm.sound_type or "beep") if alarm else "beep"
        await db.commit()

    logger.info("[ALARM] 再通知 run_id=%d attempt=%d", run_id, attempt)
    if _manager:
        await _manager.broadcast({"type": "alarm_triggered", "run_id": run_id, "message": message, "sound_type": sound_type})

    _schedule_renotify(run_id, attempt + 1)


def _schedule_renotify(run_id: int, attempt: int) -> None:
    run_date = datetime.now() + timedelta(minutes=RENOTIFY_INTERVAL_MINUTES)
    scheduler.add_job(
        check_renotify,
        DateTrigger(run_date=run_date),
        args=[run_id, attempt],
        id=f"renotify_{run_id}_{attempt}",
        replace_existing=True,
    )


async def snooze_alarm(run_id: int, minutes: int = 10) -> None:
    """スヌーズ: 現在の run を snoozed にして指定分後に再発火。"""
    async with AsyncSessionLocal() as db:
        run = await db.get(AlarmRun, run_id)
        if not run:
            return
        run.status = "snoozed"
        alarm_id = run.alarm_id
        await db.commit()

    run_date = datetime.now() + timedelta(minutes=minutes)
    scheduler.add_job(
        fire_alarm,
        DateTrigger(run_date=run_date),
        args=[alarm_id],
        id=f"snooze_{run_id}",
        replace_existing=True,
    )
    logger.info("[ALARM] スヌーズ run_id=%d %d分後に再発火", run_id, minutes)


def _cron_trigger(alarm: Alarm) -> CronTrigger:
    h, m = alarm.wake_time.split(":")
    dow = {"daily": "mon-sun", "weekday": "mon-fri", "weekend": "sat,sun"}.get(alarm.repeat_rule, "mon-sun")
    return CronTrigger(hour=int(h), minute=int(m), day_of_week=dow, timezone="Asia/Tokyo")


def register_alarm(alarm: Alarm) -> None:
    if not alarm.enabled or alarm.repeat_rule == "once":
        return
    scheduler.add_job(
        fire_alarm,
        _cron_trigger(alarm),
        args=[alarm.id],
        id=f"alarm_{alarm.id}",
        replace_existing=True,
    )
    logger.info("[ALARM] 登録 id=%d name=%s time=%s rule=%s", alarm.id, alarm.name, alarm.wake_time, alarm.repeat_rule)


def unregister_alarm(alarm_id: int) -> None:
    job_id = f"alarm_{alarm_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def fire_briefing() -> None:
    """朝のブリーフィング生成・配信。"""
    from briefing import generate_briefing, generate_audio
    logger.info("[BRIEFING] 生成開始")
    try:
        text = await generate_briefing()
    except Exception as e:
        logger.error("[BRIEFING] テキスト生成失敗: %s", e)
        return
    logger.info("[BRIEFING] テキスト: %s", text[:60])
    try:
        audio_url = await generate_audio(text)
    except Exception as e:
        logger.error("[BRIEFING] 音声生成失敗: %s", e)
        audio_url = None
    if _manager:
        await _manager.broadcast({"type": "morning_briefing", "text": text, "audio_url": audio_url})


def register_briefing(time_str: str) -> None:
    h, m = map(int, time_str.split(":"))
    job_id = "morning_briefing"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        fire_briefing,
        CronTrigger(hour=h, minute=m, timezone="Asia/Tokyo"),
        id=job_id,
    )
    logger.info("[BRIEFING] 登録 time=%s", time_str)


def unregister_briefing() -> None:
    if scheduler.get_job("morning_briefing"):
        scheduler.remove_job("morning_briefing")


async def setup_scheduler() -> None:
    """起動時に全有効アラームを登録。"""
    async with AsyncSessionLocal() as db:
        alarms = (await db.execute(select(Alarm).where(Alarm.enabled == True))).scalars().all()
    for alarm in alarms:
        register_alarm(alarm)
    logger.info("[ALARM] スケジューラー起動 %d件登録", len(alarms))

    from briefing import load_config
    config = load_config()
    if config.get("enabled", True):
        register_briefing(config["time"])
