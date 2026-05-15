import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

os.environ["TZ"] = "Asia/Jerusalem"

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.autonomous_agent import get_autonomous_agent
from api.routes import router as api_router
from db.connection import close as close_db, ping
from services.massive_realtime import MassiveRealtimeMonitor
from services.scheduler import start_scheduler, stop_scheduler
from services.telegram_bot import start_bot, stop_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("options_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    logger.info("Starting Options Agent API ...")

    ok = await ping()
    if ok:
        logger.info("MongoDB connection OK")
    else:
        logger.warning("MongoDB connection FAILED — endpoints will error until DB is reachable")

    try:
        await get_autonomous_agent().warm_up()
        logger.info("🤖 סוכן אוטונומי מוכן לפעולה")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to warm up autonomous agent")

    # Background services. Each failure is logged but never blocks API startup.
    try:
        start_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to start scheduler")

    try:
        await start_bot()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to start Telegram bot")

    massive_monitor: Optional[MassiveRealtimeMonitor] = None
    massive_task: Optional[asyncio.Task] = None
    if os.getenv("MASSIVE_API_KEY"):
        try:
            massive_monitor = MassiveRealtimeMonitor()
            massive_task = asyncio.create_task(massive_monitor.connect_and_monitor())
            logger.info("📡 Massive realtime monitor started")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to start Massive realtime monitor")
    else:
        logger.info("MASSIVE_API_KEY not set – skipping Massive realtime monitor")

    yield

    logger.info("Shutting down Options Agent API ...")
    if massive_monitor is not None and massive_task is not None:
        massive_monitor.running = False
        massive_task.cancel()
        try:
            await massive_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    try:
        await stop_bot()
    except Exception:  # noqa: BLE001
        logger.exception("Error stopping Telegram bot")

    try:
        stop_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("Error stopping scheduler")

    await close_db()


app = FastAPI(
    title="Options Agent API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "app": "Options Agent API",
        "version": "0.1.0",
        "docs": "/docs",
    }
