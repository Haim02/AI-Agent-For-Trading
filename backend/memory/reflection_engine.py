import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

from anthropic import Anthropic, APIConnectionError, APIError, APIStatusError

from db.connection import get_db
from memory.long_term import LongTermMemory
from memory.short_term import ShortTermMemory

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 2048

REFLECT_DAY_PROMPT = (
    "נתח את יום המסחר של חיים היום.\n"
    "זהה: מה עבד, מה לא עבד, מה למד חיים,\n"
    "איזה דפוסים חזרו על עצמם.\n"
    "הסק מסקנות ספציפיות לשמירה בזיכרון ארוך טווח.\n"
    "\n"
    "החזר אך ורק JSON תקין במבנה הבא (ללא טקסט מסביב):\n"
    '{"trade_learnings":[{"learning":"...","context":{"ticker":"...","strategy":"...","outcome":"win|loss|breakeven","market_conditions":"..."}}],'
    '"market_patterns":[{"pattern":"...","conditions":{"vix":"...","gex":"...","regime":"..."}}],'
    '"user_facts":[{"fact":"...","category":"preference|behavior|goal|risk_tolerance"}],'
    '"summary":"סיכום קצר בעברית"}'
)


PREF_TRIGGERS = (
    "אני לא אוהב",
    "אני אוהב",
    "אני מעדיף",
    "אני מתעדף",
    "אני שונא",
    "אף פעם לא",
    "תמיד ה",
    "תמיד אני",
    "המטרה שלי",
    "אני רוצה",
    "אני לא רוצה",
    "אני נמנע",
    "תזכור ש",
)

REFLECT_TRADE_PROMPT_TEMPLATE = (
    "נתח את העסקה הסגורה הבאה של חיים. החזר ניתוח קצר בעברית "
    "(מה עבד, מה השתבש, מה ללמוד) ואז JSON תקין במבנה:\n"
    '{{"learning":"...","context":{{"ticker":"...","strategy":"...","outcome":"win|loss|breakeven","market_conditions":"..."}},"analysis_he":"טקסט בעברית"}}\n\n'
    "פוזיציה:\n```json\n{position}\n```\n\n"
    "תנאי שוק נוכחיים:\n```json\n{market}\n```"
)


class ReflectionEngineError(RuntimeError):
    pass


class ReflectionEngine:
    """Distills daily activity into LongTermMemory entries."""

    def __init__(
        self,
        short_term: Optional[ShortTermMemory] = None,
        long_term: Optional[LongTermMemory] = None,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.short_term = short_term or ShortTermMemory()
        self.long_term = long_term or LongTermMemory()
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ReflectionEngineError("ANTHROPIC_API_KEY is not set")
        self.client = Anthropic(api_key=self.api_key)
        self.model = model

    # ───────────────────────── reflect_on_day ─────────────────────────

    async def reflect_on_day(self) -> dict[str, Any]:
        db = get_db()
        today = datetime.utcnow().date().isoformat()

        journal = await db.journal.find_one({"date": today}) or {}
        closed = await db.positions.find(
            {"status": "closed", "close_date": {"$ne": None}}
        ).sort("close_date", -1).limit(20).to_list(length=20)

        # Conversations summary — last 50 messages across all sessions touched today.
        cursor = db.conversations.find(
            {"timestamp": {"$gte": datetime.combine(datetime.utcnow().date(), datetime.min.time())}},
            {"_id": 0, "role": 1, "content": 1, "session_id": 1, "metadata": 1},
        ).sort("timestamp", 1).limit(200)
        messages = await cursor.to_list(length=200)

        payload = {
            "date": today,
            "journal": _safe_dump(journal),
            "closed_positions": [_safe_dump(p) for p in closed],
            "conversation_messages": _safe_dump(messages),
        }

        prompt = REFLECT_DAY_PROMPT + "\n\nנתונים:\n```json\n" + json.dumps(payload, ensure_ascii=False, default=str, indent=2) + "\n```"

        reply = self._claude(prompt)
        parsed = _parse_json_object(reply) or {}
        saved = self._persist_distilled(parsed)
        return {
            "date": today,
            "summary": parsed.get("summary", ""),
            "saved": saved,
            "raw_response": reply,
        }

    # ───────────────────────── reflect_on_trade ─────────────────────────

    async def reflect_on_trade(self, position_id: str) -> dict[str, Any]:
        from bson import ObjectId
        from bson.errors import InvalidId

        db = get_db()
        try:
            oid = ObjectId(position_id)
        except (InvalidId, TypeError) as exc:
            raise ReflectionEngineError(f"Invalid position id: {position_id}") from exc

        position = await db.positions.find_one({"_id": oid})
        if not position:
            raise ReflectionEngineError(f"Position not found: {position_id}")

        market = await self._market_snapshot(position)

        prompt = REFLECT_TRADE_PROMPT_TEMPLATE.format(
            position=json.dumps(_safe_dump(position), ensure_ascii=False, default=str, indent=2),
            market=json.dumps(market, ensure_ascii=False, default=str, indent=2),
        )
        reply = self._claude(prompt)
        parsed = _parse_json_object(reply) or {}

        learning = parsed.get("learning")
        if learning:
            self.long_term.save_trade_learning(
                learning=learning,
                context=parsed.get("context") or {
                    "ticker": position.get("ticker"),
                    "strategy": position.get("strategy"),
                    "outcome": _outcome(position),
                },
            )

        return {
            "position_id": position_id,
            "analysis_he": parsed.get("analysis_he") or reply,
            "learning_saved": bool(learning),
        }

    # ───────────────────────── extract_user_preferences ─────────────────────────

    def extract_user_preferences(self, conversation_history: list[dict[str, str]]) -> list[str]:
        """Cheap, model-free pass: triggers on phrases that signal a stable preference."""
        saved: list[str] = []
        for msg in conversation_history or []:
            if msg.get("role") != "user":
                continue
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            for trigger in PREF_TRIGGERS:
                if trigger in content:
                    fact = content if len(content) <= 240 else content[:240] + "…"
                    category = _category_for_trigger(trigger)
                    try:
                        self.long_term.save_user_fact(fact, category=category)
                        saved.append(fact)
                    except Exception:  # noqa: BLE001
                        logger.exception("Failed to save user fact")
                    break
        if saved:
            logger.info("Extracted %d user preferences", len(saved))
        return saved

    # ───────────────────────── internals ─────────────────────────

    def _claude(self, prompt: str) -> str:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        except (APIConnectionError, APIStatusError, APIError) as exc:
            logger.error("Reflection Claude error: %s", exc)
            raise ReflectionEngineError(f"Anthropic request failed: {exc}") from exc

        chunks: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                chunks.append(getattr(block, "text", ""))
        return "".join(chunks).strip()

    async def _market_snapshot(self, position: dict[str, Any]) -> dict[str, Any]:
        db = get_db()
        entry_date = position.get("entry_date")
        close_date = position.get("close_date")
        snapshot: dict[str, Any] = {
            "vix_at_entry": position.get("vix_at_entry"),
            "gex_regime_at_entry": position.get("gex_regime_at_entry"),
        }
        if close_date:
            day = close_date.date().isoformat() if hasattr(close_date, "date") else str(close_date)[:10]
            j = await db.journal.find_one({"date": day})
            if j:
                snapshot["close_day_journal"] = {
                    "vix_open": j.get("vix_open"),
                    "vix_close": j.get("vix_close"),
                    "spx_change_pct": j.get("spx_change_pct"),
                    "gex_regime": j.get("gex_regime"),
                }
        snapshot["entry_date"] = entry_date
        snapshot["close_date"] = close_date
        return snapshot

    def _persist_distilled(self, parsed: dict[str, Any]) -> dict[str, int]:
        saved = {"trade_learnings": 0, "market_patterns": 0, "user_facts": 0}

        for item in parsed.get("trade_learnings") or []:
            text = (item or {}).get("learning")
            if not text:
                continue
            self.long_term.save_trade_learning(text, (item or {}).get("context") or {})
            saved["trade_learnings"] += 1

        for item in parsed.get("market_patterns") or []:
            text = (item or {}).get("pattern")
            if not text:
                continue
            self.long_term.save_market_pattern(text, (item or {}).get("conditions") or {})
            saved["market_patterns"] += 1

        for item in parsed.get("user_facts") or []:
            fact = (item or {}).get("fact")
            if not fact:
                continue
            cat = (item or {}).get("category") or "preference"
            self.long_term.save_user_fact(fact, cat)
            saved["user_facts"] += 1

        return saved


# ───────────────────────── module helpers ─────────────────────────

def _outcome(position: dict[str, Any]) -> str:
    pnl = position.get("realized_pnl")
    if pnl is None:
        return "unknown"
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "breakeven"


def _category_for_trigger(trigger: str) -> str:
    if any(s in trigger for s in ("מעדיף", "אוהב", "שונא", "מתעדף", "נמנע")):
        return "preference"
    if "מטרה" in trigger or "רוצה" in trigger:
        return "goal"
    if "תמיד" in trigger or "אף פעם" in trigger:
        return "behavior"
    return "preference"


def _parse_json_object(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start == -1:
        return None
    candidate = candidate[start:]
    end = candidate.rfind("}")
    if end == -1:
        return None
    snippet = candidate[: end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        logger.debug("Reflection JSON parse failed; raw text retained")
        return None


def _safe_dump(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _safe_dump(v) for k, v in data.items() if k != "_id"}
    if isinstance(data, list):
        return [_safe_dump(v) for v in data]
    try:
        json.dumps(data)
        return data
    except TypeError:
        return str(data)
