"""LangGraph nodes for the autonomous trading agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Optional

from anthropic import Anthropic
from zoneinfo import ZoneInfo

from agent.state import AgentState, MAX_ITERATIONS
from agent.tools import TOOL_REGISTRY, list_tool_descriptions
from memory.long_term import LongTermMemory
from memory.short_term import ShortTermMemory

logger = logging.getLogger(__name__)
EST = ZoneInfo("America/New_York")

CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


SYSTEM_PROMPT = """אתה סוכן מסחר אוטונומי שעובד עבור חיים – סוחר אופציות פעיל בישראל.
היעד שלך: לעזור לחיים לקבל החלטות מסחר חכמות ובטוחות, בעברית בלבד.

עקרונות:
- כל ניתוח מתחיל בבדיקת GEX, VIX, ושעות שוק.
- אל תמליץ Iron Condor כש-GEX שלילי.
- אל תיתן סטרייקים שחוצים את Gamma Flip Level.
- שאף DTE 30-45 (או 0DTE במקרים מתאימים בלבד).
- היזהר עם Earnings קרובים.

יש לך כלים שונים שתוכל לקרוא להם בעזרת JSON.
אתה צריך לבחור כלי אחד או לסיים ולענות לחיים."""


THINK_INSTRUCTIONS = """החזר JSON תקין בלבד בלי טקסט נוסף. הסכמה:
{
  "reasoning": "מחשבה קצרה בעברית מה אתה הולך לעשות ולמה",
  "action": "respond" | "<tool_name>",
  "tool_input": {...},   // אובייקט פרמטרים לכלי, או null אם action=respond
  "response": "..."      // התשובה הסופית בעברית לחיים – חובה כש-action=respond
}

אם אתה יודע את התשובה ישירות (שאלה כללית, שיחה, הסבר) – החזר action="respond"
ומלא את שדה response עם התשובה המלאה לחיים בעברית. אל תחזיר response ריק.
אם צריך מידע חי מהשוק – בחר כלי אחד מהרשימה."""


def _now_est() -> datetime:
    return datetime.now(EST)


def _is_market_hours() -> bool:
    now = _now_est()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


def _extract_text(response: Any) -> str:
    chunks: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            chunks.append(getattr(block, "text", ""))
    return "".join(chunks).strip()


def _parse_action_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    candidates: list[str] = []
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates.extend(fenced)
    brace = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for raw in candidates:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


_JSON_BLOCK_RE = re.compile(r"```json.*?```", flags=re.DOTALL | re.IGNORECASE)
_CODE_BLOCK_RE = re.compile(r"```.*?```", flags=re.DOTALL)
_TOOL_JSON_RE = re.compile(r"\{[^{}]*\"(?:tool|action|tool_input)\"[^{}]*\}", flags=re.DOTALL)


def _clean_response_text(text: str) -> str:
    """Strip any JSON fragments that may have leaked into a user-facing reply."""
    if not text:
        return ""
    cleaned = _JSON_BLOCK_RE.sub("", text)
    cleaned = _CODE_BLOCK_RE.sub("", cleaned)
    cleaned = _TOOL_JSON_RE.sub("", cleaned)
    return cleaned.strip()


def _looks_like_tool_json(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    return stripped.startswith("{") and ('"action"' in stripped or '"tool"' in stripped)


# ───────────────────────── nodes ─────────────────────────

async def perceive_node(state: AgentState) -> AgentState:
    logger.info("🔍 תופס מצב שוק נוכחי...")
    now = _now_est()
    market_context = {
        "now_est": now.isoformat(),
        "market_open": _is_market_hours(),
        "weekday": now.strftime("%A"),
    }
    # Recall a few memories that match the user message – cheap context bump.
    def _recall() -> dict:
        try:
            return LongTermMemory().recall_all(state.get("user_message", ""), n_results=2)
        except Exception:  # noqa: BLE001
            logger.exception("perceive_node: memory recall failed")
            return {}

    memories = await asyncio.to_thread(_recall)
    market_context["memories"] = memories
    state["market_context"] = market_context
    return state


async def think_node(state: AgentState) -> AgentState:
    iteration = state.get("iteration_count", 0)
    state["iteration_count"] = iteration + 1
    logger.info("🧠 חושב (איטרציה %d)...", state["iteration_count"])

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        state["next_action"] = "respond"
        state["errors"].append("ANTHROPIC_API_KEY חסר")
        state["response"] = "⚠️ לא הוגדר ANTHROPIC_API_KEY – לא ניתן להפעיל את הסוכן."
        return state

    # Build a real multi-turn messages array. The Anthropic API requires strict
    # user/assistant alternation, so we collapse consecutive same-role turns.
    history_msgs: list[dict[str, str]] = []
    for msg in (state.get("messages") or [])[-20:]:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if history_msgs and history_msgs[-1]["role"] == role:
            history_msgs[-1]["content"] += "\n\n" + content
        else:
            history_msgs.append({"role": role, "content": content})

    # If history ends on assistant, that's fine — we'll append the new user turn.
    # If it ends on user (no assistant reply yet), drop that trailing entry to
    # avoid two consecutive user messages once we append the current turn.
    if history_msgs and history_msgs[-1]["role"] == "user":
        history_msgs.pop()

    context_text = json.dumps(state.get("market_context", {}), ensure_ascii=False, default=str)
    last_tool = state.get("last_tool_result") or "(טרם הופעל כלי)"
    analysis = state.get("analysis_results") or {}
    analysis_text = (
        json.dumps(_jsonable(analysis), ensure_ascii=False, default=str)
        if analysis else "(טרם נאסף מידע)"
    )

    current_turn = (
        f"הודעת חיים: {state.get('user_message', '')}\n\n"
        f"הקשר שוק:\n{context_text}\n\n"
        f"מידע שנאסף בסבבים קודמים:\n{analysis_text}\n\n"
        f"תוצאת הכלי האחרון:\n{last_tool}\n\n"
        f"כלים זמינים:\n{list_tool_descriptions()}\n\n"
        f"{THINK_INSTRUCTIONS}"
    )

    messages_for_claude = history_msgs + [{"role": "user", "content": current_turn}]

    try:
        client = Anthropic(api_key=api_key)
        response = await asyncio.to_thread(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages_for_claude,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("think_node: Claude call failed")
        state["errors"].append(f"Claude error: {exc}")
        state["next_action"] = "respond"
        state["response"] = state.get("response") or "⚠️ שגיאה בקריאה ל-Claude."
        return state

    text = _extract_text(response)
    parsed = _parse_action_json(text)
    if not parsed:
        logger.warning("think_node: failed to parse JSON, falling back to respond")
        state["next_action"] = "respond"
        # Claude wrote plain prose – keep it, but strip anything that resembles
        # raw tool JSON so we never echo control structures to the user.
        state["response"] = _clean_response_text(text) or state.get("response") or ""
        return state

    reasoning = parsed.get("reasoning", "")
    if reasoning:
        logger.info("💭 %s", reasoning)
    # Accept either "action" (our schema) or "tool" (some Claude variants)
    action = (parsed.get("action") or parsed.get("tool") or "respond").strip()
    tool_input = parsed.get("tool_input") or parsed.get("params") or parsed.get("tool_params")

    if action == "respond":
        # Claude decided to answer directly – use the answer field if present.
        direct_answer = (
            parsed.get("response")
            or parsed.get("answer")
            or parsed.get("message")
            or ""
        ).strip()
        if not direct_answer and reasoning:
            direct_answer = reasoning
        # Strip any embedded JSON to be extra safe.
        direct_answer = _clean_response_text(direct_answer)
        state["next_action"] = "respond"
        state["decision"] = reasoning
        state["response"] = direct_answer or state.get("response") or ""
        logger.info("✅ think_node: respond ready (len=%d)", len(state["response"] or ""))
        return state

    if action not in TOOL_REGISTRY:
        logger.warning("think_node: unknown tool %s", action)
        state["errors"].append(f"כלי לא מוכר: {action}")
        state["next_action"] = "respond"
        # Don't let an unknown tool's JSON envelope leak into the response.
        existing = state.get("response") or ""
        if _looks_like_tool_json(existing):
            state["response"] = ""
        return state

    # Tool was chosen – make sure no JSON envelope is still sitting in response.
    state["next_action"] = action
    state["tool_input"] = tool_input
    state["tool_params"] = tool_input if isinstance(tool_input, dict) else {}
    state["decision"] = reasoning
    existing = state.get("response") or ""
    if _looks_like_tool_json(existing):
        state["response"] = ""
    logger.info("🛠 think_node: chose tool=%s params=%s", action, state["tool_params"])
    return state


_RESULT_BUCKET = {
    "scan_iv_opportunities": "iv_scan",
    "get_iv_rank": "iv_scan",
    "web_search": "web_search",
    "market_overview": "market",
    "research_stock": "research",
    "get_gex_levels": "gex",
    "analyze_ticker": "analyze",
    "check_trade_conditions": "trade_conditions",
    "get_open_positions": "positions",
    "get_market_news": "news",
    "recall_memory": "memory",
    "check_earnings": "earnings_risk",
    "get_analyst_recommendations": "analyst_recs",
    "get_stock_news": "stock_news",
    "get_earnings_calendar": "earnings_calendar",
    "full_ticker_analysis": "ticker_analysis",
}


async def act_node(state: AgentState) -> AgentState:
    action = state.get("next_action", "")
    tool = TOOL_REGISTRY.get(action)
    if tool is None:
        state["last_tool_result"] = "❌ לא נבחר כלי"
        state["next_action"] = "respond"
        return state

    logger.info("⚡ מבצע: %s...", action)
    # Prefer the dict params we set up in think_node, fall back to raw tool_input.
    payload = state.get("tool_params")
    if not payload:
        payload = state.get("tool_input")
    try:
        result = await tool.run(payload)
    except Exception as exc:  # noqa: BLE001
        logger.exception("act_node: %s failed", action)
        state["errors"].append(f"{action}: {exc}")
        state["last_tool_result"] = f"❌ כשל בכלי {action}: {exc}"
        state["next_action"] = "respond"
        return state

    state["last_tool_result"] = result
    if action == "scan_market":
        state["scan_results"] = [{"summary": result}]
    elif action == "calculate_strategy":
        state["strategy_params"] = {"summary": result}

    # Always merge into analysis_results under a stable bucket key so respond_node
    # can format a labelled section.
    bucket = _RESULT_BUCKET.get(action, action)
    analysis = state.get("analysis_results") or {}
    analysis[bucket] = result
    state["analysis_results"] = analysis

    # After a single tool call go straight to the final answer. (The graph's
    # observe_node may still route back to think for multi-step flows.)
    state["next_action"] = "respond"
    return state


async def observe_node(state: AgentState) -> AgentState:
    iteration = state.get("iteration_count", 0)
    if iteration >= MAX_ITERATIONS:
        logger.info("🔁 הגענו לתקרת איטרציות – עוברים לתשובה")
        state["next_action"] = "respond"
        return state

    last = state.get("last_tool_result") or ""
    if last.startswith("❌"):
        # Bail out – the tool failed, no point retrying blindly.
        state["next_action"] = "respond"
        return state

    # By default go back to think; think_node will decide whether to call
    # another tool or to respond.
    state["next_action"] = "think"
    return state


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _last_assistant_message(state: AgentState) -> str:
    for msg in reversed(state.get("messages", []) or []):
        if msg.get("role") == "assistant":
            content = (msg.get("content") or "").strip()
            if content:
                return content
    return ""


def _is_generic_response(text: str) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    generic = {
        "אין מספיק מידע כרגע",
        "אין מספיק מידע כרגע.",
        "(אין תשובה)",
    }
    return stripped in generic


_BUCKET_LABELS = {
    "iv_scan": "תוצאות סריקת IV",
    "web_search": "תוצאות חיפוש אינטרנט",
    "market": "מצב שוק",
    "research": "מחקר מניה",
    "gex": "נתוני GEX",
    "analyze": "ניתוח מניה",
    "trade_conditions": "בדיקת תנאי עסקה",
    "positions": "פוזיציות פתוחות",
    "news": "חדשות שוק",
    "memory": "זיכרון",
    "earnings_risk": "סיכון Earnings",
    "analyst_recs": "המלצות אנליסטים",
    "stock_news": "חדשות מניה",
    "earnings_calendar": "לוח Earnings",
    "ticker_analysis": "ניתוח מלא של מניה",
}


async def respond_node(state: AgentState) -> AgentState:
    logger.info("💬 respond_node: building final Hebrew answer…")
    try:
        snapshot = {
            "user_message": state.get("user_message"),
            "next_action": state.get("next_action"),
            "iteration_count": state.get("iteration_count"),
            "decision": state.get("decision"),
            "response_draft": (state.get("response") or "")[:200],
            "analysis_keys": list((state.get("analysis_results") or {}).keys()),
            "errors": state.get("errors"),
        }
        logger.info("📦 %s", json.dumps(snapshot, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        logger.exception("respond_node: state dump failed")

    user_message = state.get("user_message", "")
    results = state.get("analysis_results") or {}
    scan_results = state.get("scan_results") or []
    strategy_params = state.get("strategy_params") or {}
    draft = (state.get("response") or "").strip()
    if _looks_like_tool_json(draft):
        logger.warning("respond_node: dropping JSON draft from state.response")
        draft = ""

    context_parts: list[str] = []
    for key, label in _BUCKET_LABELS.items():
        if key in results and results[key]:
            value = results[key]
            text_val = value if isinstance(value, str) else json.dumps(
                _jsonable(value), ensure_ascii=False, default=str
            )
            context_parts.append(f"{label}:\n{text_val}")
    # Catch any custom buckets that aren't in the label map.
    for key, value in results.items():
        if key in _BUCKET_LABELS or not value:
            continue
        text_val = value if isinstance(value, str) else json.dumps(
            _jsonable(value), ensure_ascii=False, default=str
        )
        context_parts.append(f"{key}:\n{text_val}")
    if scan_results:
        context_parts.append(
            "תוצאות סריקה:\n"
            + json.dumps(_jsonable(scan_results), ensure_ascii=False, default=str)
        )
    if strategy_params:
        context_parts.append(
            "פרמטרי אסטרטגיה:\n"
            + json.dumps(_jsonable(strategy_params), ensure_ascii=False, default=str)
        )

    context = "\n\n".join(context_parts) or "ענה מהידע שלך"

    prompt = (
        f"חיים שאל: {user_message}\n\n"
        f"המידע שנאסף:\n{context}\n\n"
        "כתוב תשובה מסודרת בעברית עם:\n"
        "- כותרת ברורה\n"
        "- נתונים מהמידע שנאסף\n"
        "- המלצה מעשית\n"
        "- אימוג'ים לקריאות\n\n"
        "אל תכלול JSON בתשובה!"
    )

    response_text = ""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            client = Anthropic(api_key=api_key)
            response = await asyncio.to_thread(
                client.messages.create,
                model=CLAUDE_MODEL,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = _extract_text(response)
            logger.info("🟢 respond_node: Claude returned %d chars", len(response_text))
        except Exception:  # noqa: BLE001
            logger.exception("respond_node: Claude call failed")
    else:
        logger.warning("respond_node: ANTHROPIC_API_KEY missing – skipping Claude call")

    response_text = _clean_response_text(response_text)

    if not response_text or len(response_text) < 10 or _is_generic_response(response_text):
        response_text = (
            draft
            or _clean_response_text(state.get("last_tool_result") or "")
            or _last_assistant_message(state)
            or "מצטער חיים, נתקלתי בבעיה טכנית. נסה שוב."
        )

    state["response"] = response_text
    state["next_action"] = "learn"

    # Persist the exchange to short-term memory (best-effort).
    session_id = state.get("session_id", "default")
    short = ShortTermMemory()
    try:
        await short.save_message(session_id, "user", user_message)
        await short.save_message(session_id, "assistant", response_text)
    except Exception:  # noqa: BLE001
        logger.exception("respond_node: short-term memory persist failed")

    state.setdefault("messages", []).extend(
        [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response_text},
        ]
    )
    return state


async def learn_node(state: AgentState) -> AgentState:
    response = state.get("response") or ""
    if len(response) < 80:
        return state

    def _persist() -> None:
        try:
            ltm = LongTermMemory()
            ltm.save_knowledge(
                f"Q: {state.get('user_message', '')}\nA: {response[:500]}",
                metadata={"category": "agent_interaction", "timestamp": datetime.utcnow().isoformat()},
            )
        except Exception:  # noqa: BLE001
            logger.exception("learn_node: long-term persist failed")

    await asyncio.to_thread(_persist)
    return state


__all__ = [
    "perceive_node",
    "think_node",
    "act_node",
    "observe_node",
    "respond_node",
    "learn_node",
]
