import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.autonomous_agent import get_autonomous_agent
from api.routes import router as api_router
from db.connection import close as close_db, ping
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

    yield

    logger.info("Shutting down Options Agent API ...")
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

origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "https://ai-agent-for-trading-fvvtmsshu-haims-projects-4c08280d.vercel.app",
]

extra_origins = os.getenv("CORS_ORIGINS", "")
if extra_origins:
    origins.extend(o.strip() for o in extra_origins.split(",") if o.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
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
