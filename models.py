from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base

class Alarm(Base):
    __tablename__ = "alarms"
    id            = Column(Integer, primary_key=True)
    name          = Column(String, nullable=False)
    wake_time     = Column(String, nullable=False)   # "HH:MM"
    repeat_rule   = Column(String, default="daily")  # daily / weekday / weekend / once
    enabled       = Column(Boolean, default=True)
    strictness_level = Column(Integer, default=2)    # 1〜5
    voice_style   = Column(String, default="friendly")
    sound_type    = Column(String, default="beep")   # beep/double/rising/gentle/<filename.mp3>
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow)
    runs          = relationship("AlarmRun", back_populates="alarm", cascade="all, delete-orphan")

class AlarmRun(Base):
    __tablename__ = "alarm_runs"
    id              = Column(Integer, primary_key=True)
    alarm_id        = Column(Integer, ForeignKey("alarms.id"))
    scheduled_at    = Column(DateTime)
    triggered_at    = Column(DateTime)
    acknowledged_at = Column(DateTime)
    renotify_count  = Column(Integer, default=0)
    status          = Column(String, default="pending")  # pending/ringing/acknowledged/missed/snoozed
    alarm           = relationship("Alarm", back_populates="runs")
    messages        = relationship("GeneratedMessage", back_populates="run", cascade="all, delete-orphan")

class GeneratedMessage(Base):
    __tablename__ = "generated_messages"
    id           = Column(Integer, primary_key=True)
    alarm_run_id = Column(Integer, ForeignKey("alarm_runs.id"))
    message_text = Column(Text)
    source       = Column(String, default="template")  # template / llm
    created_at   = Column(DateTime, default=datetime.utcnow)
    run          = relationship("AlarmRun", back_populates="messages")
