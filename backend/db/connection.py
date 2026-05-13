import logging
import os
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def _connect() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is not None:
        return _db

    mongo_url = os.getenv("MONGO_URL", "mongodb+srv://hhaim12_db_user:951753@cluster0.dvk1ryt.mongodb.net/")
    mongo_db = os.getenv("MONGO_DB", "options_agent")

    _client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
    _db = _client[mongo_db]
    return _db


def get_db() -> AsyncIOMotorDatabase:
    return _connect()


async def ping() -> bool:
    db = _connect()
    try:
        result = await db.command("ping")
        ok = bool(result.get("ok"))
        if ok:
            logger.info("MongoDB ping successful (db=%s)", db.name)
        else:
            logger.warning("MongoDB ping returned non-ok: %s", result)
        return ok
    except Exception as exc:
        logger.error("MongoDB ping failed: %s", exc)
        return False


async def close() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None
