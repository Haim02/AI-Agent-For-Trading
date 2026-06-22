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
from api.checklist_routes import router as checklist_router
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
        from memory.gex_knowledge_loader import GEXKnowledgeLoader
        count = await GEXKnowledgeLoader().load_all_knowledge()
        logger.info("✅ GEX Knowledge loaded: %d items", count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GEX Knowledge load failed: %s", exc)

    # FlashAlpha primary GEX source – verify the official SDK is importable
    # and log the active plan + daily usage so the operator can spot quota
    # issues before they bite during the trading day.
    try:
        import flashalpha as _flashalpha_pkg

        fa_key = os.getenv("FLASHALPHA_API_KEY", "")
        if fa_key:
            from flashalpha import FlashAlpha as _FlashAlpha

            fa_client = _FlashAlpha(fa_key)
            try:
                health = fa_client.health()
                logger.info(
                    "✅ FlashAlpha SDK v%s | status: %s",
                    getattr(_flashalpha_pkg, "__version__", "?"),
                    health.get("status") if health else "?",
                )
            except Exception as health_exc:  # noqa: BLE001
                logger.warning("FlashAlpha health check failed: %s", health_exc)

            try:
                acct = fa_client.account()
                plan = acct.get("plan", "unknown") if acct else "unknown"
                usage = (acct or {}).get("usage", {}) or {}
                daily = usage.get("daily_requests_used", "?")
                limit = usage.get("daily_requests_limit", "?")
                logger.info("   Plan: %s | Usage: %s/%s today", plan, daily, limit)
            except Exception as acct_exc:  # noqa: BLE001
                logger.warning("FlashAlpha account fetch failed: %s", acct_exc)
        else:
            logger.warning(
                "⚠️ FLASHALPHA_API_KEY not set – using fallback GEX sources (UW/Massive/yfinance)"
            )
    except ImportError:
        logger.warning(
            "⚠️ flashalpha package not installed. Run: pip install flashalpha"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("FlashAlpha init error: %s", exc)

    # Unusual Whales credentials check — best-effort, doesn't block startup.
    uw_email = os.getenv("UW_EMAIL", "")
    uw_pass = os.getenv("UW_PASSWORD", "")
    if uw_email and uw_pass:
        logger.info("✅ UW credentials found: %s", uw_email)

        async def _uw_warmup() -> None:
            try:
                from scrapers.uw_scraper import UnusualWhalesScraper
                scraper = UnusualWhalesScraper()
                try:
                    if await scraper._ensure_logged_in():
                        logger.info("✅ UW login successful on startup!")
                        await scraper._async_save_session()
                    else:
                        logger.warning("⚠️ UW login failed on startup")
                finally:
                    await scraper.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("UW startup check: %s", exc)

        asyncio.create_task(_uw_warmup())
    else:
        logger.warning("⚠️ UW_EMAIL/UW_PASSWORD not in .env")

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
app.include_router(checklist_router, prefix="/api")


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "app": "Options Agent API",
        "version": "0.1.0",
        "docs": "/docs",
    }
