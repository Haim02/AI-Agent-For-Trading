import asyncio
import json
import logging
import os
from typing import Any, Optional

from anthropic import Anthropic
from anthropic import APIError, APIStatusError, APIConnectionError

from agent.persona import SYSTEM_PROMPT
from memory.context_builder import ContextBuilder
from memory.long_term import LongTermMemory
from memory.reflection_engine import ReflectionEngine, ReflectionEngineError
from memory.short_term import ShortTermMemory

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_HISTORY_MESSAGES = 20
MAX_TOKENS = 2048



class ChatAgentError(RuntimeError):
    """Raised when Anthropic credentials are missing or the request fails."""


class ChatAgent:
    """Hebrew-speaking trading coach backed by Anthropic Claude."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        short_term: Optional[ShortTermMemory] = None,
        long_term: Optional[LongTermMemory] = None,
        context_builder: Optional[ContextBuilder] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ChatAgentError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self.client = Anthropic(api_key=self.api_key)

        self.short_term = short_term or ShortTermMemory()
        self.long_term = long_term or LongTermMemory()
        self.context_builder = context_builder or ContextBuilder(
            short_term=self.short_term, long_term=self.long_term
        )

    # ───────────────────────── public ─────────────────────────

    async def chat(
        self,
        message: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
        session_id: str = "default",
    ) -> dict[str, Any]:
        # 1. Build rich Hebrew context from short- and long-term memory.
        try:
            context_block = await self.context_builder.build_context(message, session_id)
        except Exception:  # noqa: BLE001
            logger.exception("ContextBuilder failed — falling back to base system prompt")
            context_block = ""

        system = SYSTEM_PROMPT
        if context_block:
            system = (
                "=== הקשר שמור (חובה להתחשב בו) ===\n"
                f"{context_block}\n"
                "=== סוף הקשר ===\n\n"
                + SYSTEM_PROMPT
            )

        # 2. Persist the user message before calling Claude.
        try:
            await self.short_term.save_message(session_id, "user", message)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist user message")

        # 3. Compose Anthropic message history (in-memory + new turn).
        history = list(conversation_history or [])
        history.append({"role": "user", "content": message})
        history = _truncate_history(history, MAX_HISTORY_MESSAGES)

        # 4. Call Claude (sync SDK) in a worker thread so we don't block the loop.
        try:
            response = await asyncio.to_thread(
                self.client.messages.create,
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=history,
            )
        except (APIConnectionError, APIStatusError, APIError) as exc:
            logger.error("ChatAgent Anthropic error: %s", exc)
            raise ChatAgentError(f"Anthropic request failed: {exc}") from exc

        reply = _extract_text(response)
        history.append({"role": "assistant", "content": reply})
        history = _truncate_history(history, MAX_HISTORY_MESSAGES)

        # 5. Persist the assistant reply.
        try:
            await self.short_term.save_message(session_id, "assistant", reply)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist assistant message")

        # 6. Background: extract any new user preferences from this turn.
        asyncio.create_task(_extract_preferences_safe(message))

        return {"response": reply, "history": history}

    def learn(self, content: str, category: str = "preference") -> dict[str, Any]:
        """Explicit teaching path — saves to LongTermMemory and returns the id."""
        category = (category or "preference").lower().strip()
        if category in ("preference", "behavior", "goal", "risk_tolerance"):
            doc_id = self.long_term.save_user_fact(content, category=category)
            collection = "user_profile"
        elif category in ("trade_learning", "lesson"):
            doc_id = self.long_term.save_trade_learning(content, context={})
            collection = "trade_learnings"
        elif category in ("market_pattern", "pattern"):
            doc_id = self.long_term.save_market_pattern(content, conditions={})
            collection = "market_patterns"
        elif category in ("strategy", "strategy_knowledge"):
            doc_id = self.long_term.save_strategy_knowledge(content, context={})
            collection = "strategy_knowledge"
        else:
            doc_id = self.long_term.save_knowledge(content, {"category": category})
            collection = "knowledge_base"
        return {"id": doc_id, "collection": collection, "category": category}

    async def analyze_position(self, position_data: dict[str, Any]) -> str:
        prompt = (
            "נתח בבקשה את הפוזיציה הבאה. בנה את התשובה בסעיפים הבאים בסדר הזה:\n"
            "\n"
            "1. *סטטוס נוכחי* — מרחק המחיר מ-strikes ומ-breakeven, השפעת VIX, ימים לתפוגה.\n"
            "2. *בדיקת GEX Regime* — מהו מצב ה-Gamma כרגע (Long / Short / Flip)? "
            "ציין אם המחיר מעל או מתחת ל-Gamma Flip Level.\n"
            "3. *מרחק מ-Gamma Wall הקרוב* — זהה את ה-Wall הרלוונטי "
            "(Call Resistance ל-call-side, Put Support ל-put-side), "
            "חשב את המרחק באחוזים מהמחיר הנוכחי, וציין האם ה-strikes של הפוזיציה "
            "עדיין מעבר ל-Walls או שכבר נחצו.\n"
            "4. *הערכת סיכון GEX-מבוססת* — חבר את ה-Regime עם המרחק ל-Walls:\n"
            "   - Long Gamma + מרחק >0.5% מהקיר → סיכון נמוך\n"
            "   - Short Gamma או מרחק <0.3% מהקיר → סיכון גבוה\n"
            "   - חציית Gamma Flip → סיכון קריטי\n"
            "5. *המלצה* — להחזיק / לסגור / לגלגל / להתאים.\n"
            "   אם המחיר קרוב ל-Gamma Wall (פחות מ-0.3%) או חצה אותו → "
            "המלץ במפורש על התאמה (roll / סגירת צד / הקטנת חשיפה) והסבר מדוע.\n"
            "6. *רמת סיכון כוללת* — נמוך / בינוני / גבוה / קריטי.\n"
            "\n"
            "נתוני הפוזיציה:\n"
            "```json\n"
            f"{json.dumps(_safe_dump(position_data), ensure_ascii=False, indent=2)}\n"
            "```"
        )
        result = await self.chat(prompt, session_id="position_analysis")
        return result["response"]


# ───────────────────────── internals ─────────────────────────

def _truncate_history(history: list[dict[str, str]], max_messages: int) -> list[dict[str, str]]:
    if len(history) <= max_messages:
        return history
    return history[-max_messages:]


def _extract_text(response: Any) -> str:
    chunks: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            chunks.append(getattr(block, "text", ""))
    return "".join(chunks).strip() or "(לא התקבל מענה)"


def _safe_dump(data: Any) -> Any:
    """Convert non-JSON-serializable types (datetime, ObjectId) into strings."""
    if isinstance(data, dict):
        return {k: _safe_dump(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_safe_dump(v) for v in data]
    try:
        json.dumps(data)
        return data
    except TypeError:
        return str(data)


async def _extract_preferences_safe(user_message: str) -> None:
    """Run ReflectionEngine.extract_user_preferences off the request hot path."""
    try:
        engine = ReflectionEngine()
    except ReflectionEngineError as exc:
        logger.debug("Reflection engine unavailable: %s", exc)
        return
    try:
        await asyncio.to_thread(
            engine.extract_user_preferences,
            [{"role": "user", "content": user_message}],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Background preference extraction failed")
