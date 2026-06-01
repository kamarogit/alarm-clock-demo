import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./alarm.db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def init_db():
    from models import Alarm, AlarmRun, GeneratedMessage
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # migrate: add sound_type column to existing DBs
        try:
            await conn.execute(text("ALTER TABLE alarms ADD COLUMN sound_type VARCHAR DEFAULT 'beep'"))
        except Exception:
            pass

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
