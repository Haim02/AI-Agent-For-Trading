"""LangGraph nodes for the autonomous trading agent.

Flow: perceive → think ⇄ act/observe → respond → learn

The think/act loop uses the Anthropic *native* tool-use API: Claude returns
structured ``tool_use`` blocks, we execute them and feed ``tool_result``
blocks back into the same conversation thread. The final Hebrew answer is
whatever Claude writes when it stops calling tools — no second "formatting"
call, no JSON-in-prose parsing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

from anthropic import AsyncAnthropic
from zoneinfo import ZoneInfo

from agent.persona import SYSTEM_PROMPT
from agent.state import AgentState, MAX_ITERATIONS
from agent.tools import TOOL_REGISTRY, anthropic_tool_specs
from memory.long_term import LongTermMemory
from memory.short_term import ShortTermMemory

logger = logging.getLogger(__name__)
EST = ZoneInfo("America/New_York")

CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_TOKENS = 4096
TOOL_RESULT_MAX_CHARS = 8000

_client: Optional[AsyncAnthropic] = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _now_est() -> datetime:
    return datetime.now(EST)


def _is_market_hours() -> bool:
    now = _now_est()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


_MD_CLEAN_RE = re.compile(r"```.*?```", flags=re.DOTALL)


def _clean_response_text(text: str) -> str:
    """Drop code fences that would break Telegram rendering."""
    if not text:
        return ""
    return _MD_CLEAN_RE.sub("", text).strip()


def _blocks_to_dicts(blocks: Any) -> list[dict[str, Any]]:
    """Serialize SDK content blocks into plain dicts for the messages array."""
    out: list[dict[str, Any]] = []
    for block in blocks or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            out.append({"type": "text", "text": getattr(block, "text", "")})
        elif btype == "tool_use":
            out.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", {}) or {},
                }
            )
    return out


def _text_from_blocks(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(
        b.get("text", "") for b in blocks if b.get("type") == "text"
    ).strip()


def _collapse_history(raw: list[dict[str, Any]], limit: int = 20) -> list[dict[str, str]]:
    """History → strictly alternating user/assistant text turns."""
    msgs: list[dict[str, str]] = []
    for msg in (raw or [])[-limit:]:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + content
        else:
            msgs.append({"role": role, "content": content})
    # The current user turn is appended separately – avoid a double user turn.
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    return msgs


# ───────────────────────── nodes ─────────────────────────

async def perceive_node(state: AgentState) -> AgentState:
    """Gather everything the trader-brain should know before thinking:
    market clock, rich memory context (profile, lessons, patterns, positions,
    recent conversation) and GEX knowledge relevant to the question."""
    logger.info("🔍 perceive: building memory + market context")
    now = _now_est()
    state["market_context"] = {
        "now_est": now.isoformat(),
        "market_open": _is_market_hours(),
        "weekday": now.strftime("%A"),
    }

    user_message = state.get("user_message", "")
    session_id = state.get("session_id", "default")

    # 1) Rich Hebrew memory context – same builder the fallback path uses.
    memory_context = ""
    try:
        from memory.context_builder import ContextBuilder

        memory_context = await ContextBuilder().build_context(user_message, session_id)
    except Exception:  # noqa: BLE001
        logger.exception("perceive: ContextBuilder failed – degrading to raw recall")
        try:
            recall = await asyncio.to_thread(
                lambda: LongTermMemory().recall_all(user_message, n_results=3)
            )
            parts = []
            for collection, items in (recall or {}).items():
                for item in items or []:
                    parts.append(f"[{collection}] {item.get('content', '')}")
            memory_context = "\n".join(parts)
        except Exception:  # noqa: BLE001
            logger.exception("perceive: raw recall failed too")

    # 2) GEX/flow knowledge base (RAG) – matched to the question.
    knowledge = ""
    try:
        from memory.gex_knowledge_loader import GEXKnowledgeLoader

        relevant = await asyncio.to_thread(
            GEXKnowledgeLoader().query_gex_knowledge, user_message, 2
        )
        if relevant:
            knowledge = "\n\n=== ידע מקצועי רלוונטי ===\n" + "\n".join(
                f"{item.get('topic') or '—'}: {(item.get('content') or '')[:400]}"
                for item in relevant
            )
    except Exception:  # noqa: BLE001
        logger.exception("perceive: GEX knowledge retrieval failed")

    # 3) Trading library (NotebookLM courses/guides) – best matching excerpts.
    try:
        from memory.trading_library import TradingLibrary

        excerpts = await asyncio.to_thread(TradingLibrary().query, user_message, 3)
        if excerpts:
            knowledge += "\n\n=== מהספרייה המקצועית (NotebookLM) ===\n" + "\n---\n".join(
                f"[{item.get('notebook') or '—'} / {item.get('doc_title') or '—'}]\n"
                f"{(item.get('content') or '')[:600]}"
                for item in excerpts
            )
    except Exception:  # noqa: BLE001
        logger.exception("perceive: trading library retrieval failed")

    state["memory_context"] = (memory_context + knowledge).strip()
    return state


async def think_node(state: AgentState) -> AgentState:
    iteration = state.get("iteration_count", 0)
    state["iteration_count"] = iteration + 1
    logger.info("🧠 think (iteration %d)", state["iteration_count"])

    if not os.getenv("ANTHROPIC_API_KEY"):
        state["next_action"] = "respond"
        state["errors"].append("ANTHROPIC_API_KEY חסר")
        state["response"] = "⚠️ לא הוגדר ANTHROPIC_API_KEY – לא ניתן להפעיל את הסוכן."
        return state

    claude_messages: list[dict[str, Any]] = state.get("claude_messages") or []

    # First iteration: assemble history + context + current question.
    if not claude_messages:
        now = _now_est()
        context_header = (
            f"[מצב שוק: {'פתוח' if state['market_context'].get('market_open') else 'סגור'} | "
            f"{now.strftime('%A %H:%M')} EST]"
        )
        memory_block = state.get("memory_context") or ""
        current_turn = state.get("user_message", "")
        if memory_block:
            current_turn = (
                "=== הקשר שמור (זיכרון הסוכן – קרא לפני שאתה עונה) ===\n"
                f"{memory_block}\n"
                "=== סוף הקשר ===\n\n"
                f"{context_header}\n\n"
                f"חיים כותב: {current_turn}"
            )
        else:
            current_turn = f"{context_header}\n\nחיים כותב: {current_turn}"

        claude_messages = _collapse_history(state.get("messages") or [])
        claude_messages.append({"role": "user", "content": current_turn})
        state["claude_messages"] = claude_messages

    try:
        response = await _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=anthropic_tool_specs(),
            messages=claude_messages,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("think: Claude call failed")
        state["errors"].append(f"Claude error: {exc}")
        state["next_action"] = "respond"
        state["response"] = state.get("response") or (
            "נתקלתי בתקלה טכנית מול המודל. נסה שוב בעוד רגע."
        )
        return state

    blocks = _blocks_to_dicts(response.content)
    claude_messages.append({"role": "assistant", "content": blocks})
    state["claude_messages"] = claude_messages

    tool_calls = [b for b in blocks if b.get("type") == "tool_use"]
    text = _text_from_blocks(blocks)

    if response.stop_reason == "tool_use" and tool_calls:
        if text:
            logger.info("💭 %s", text[:200])
            state["decision"] = text
        state["pending_tools"] = tool_calls
        state["next_action"] = "act"
        logger.info(
            "🛠 think: chose tools %s", [t.get("name") for t in tool_calls]
        )
        return state

    # No tool call → this is the final answer.
    state["response"] = _clean_response_text(text)
    state["next_action"] = "respond"
    logger.info("✅ think: final answer ready (%d chars)", len(state["response"] or ""))
    return state


async def act_node(state: AgentState) -> AgentState:
    pending = state.get("pending_tools") or []
    if not pending:
        state["next_action"] = "respond"
        return state

    results: list[dict[str, Any]] = []
    analysis = state.get("analysis_results") or {}

    for call in pending:
        name = call.get("name", "")
        tool = TOOL_REGISTRY.get(name)
        tool_input = call.get("input") or {}
        logger.info("⚡ act: %s(%s)", name, tool_input)

        if tool is None:
            output = f"❌ כלי לא מוכר: {name}"
        else:
            try:
                output = await tool.run(tool_input)
            except Exception as exc:  # noqa: BLE001
                logger.exception("act: %s failed", name)
                state["errors"].append(f"{name}: {exc}")
                output = f"❌ הכלי {name} נכשל: {exc}"

        output = (output or "")[:TOOL_RESULT_MAX_CHARS]
        state["last_tool_result"] = output
        analysis[name] = output
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": call.get("id", ""),
                "content": output,
            }
        )

    state["analysis_results"] = analysis
    claude_messages = state.get("claude_messages") or []
    claude_messages.append({"role": "user", "content": results})
    state["claude_messages"] = claude_messages
    state["pending_tools"] = []
    state["next_action"] = "think"
    return state


async def observe_node(state: AgentState) -> AgentState:
    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        logger.info("🔁 iteration cap reached – forcing final answer")
        state["next_action"] = "respond"
        return state
    if state.get("next_action") == "respond":
        return state
    state["next_action"] = "think"
    return state


async def respond_node(state: AgentState) -> AgentState:
    """Finalize: if the loop was cut off mid-research, ask Claude to wrap up
    from what was gathered (no tools). Then persist the exchange."""
    response_text = (state.get("response") or "").strip()

    if not response_text:
        claude_messages = list(state.get("claude_messages") or [])
        if claude_messages and os.getenv("ANTHROPIC_API_KEY"):
            # The last message may be an assistant tool_use turn. The API
            # requires every tool_use to get a tool_result before any new
            # text – answer the dangling calls with a "cancelled" result,
            # then ask for the wrap-up in the same user turn.
            if claude_messages[-1]["role"] == "assistant":
                last_content = claude_messages[-1].get("content")
                dangling = [
                    b for b in (last_content if isinstance(last_content, list) else [])
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                ]
                wrap_up: list[dict[str, Any]] = [
                    {
                        "type": "tool_result",
                        "tool_use_id": b.get("id", ""),
                        "content": "בוטל – הגענו למגבלת מחקר.",
                    }
                    for b in dangling
                ]
                wrap_up.append(
                    {
                        "type": "text",
                        "text": (
                            "עצור את המחקר. סכם עכשיו תשובה סופית לחיים בעברית "
                            "על בסיס כל המידע שנאסף עד כה."
                        ),
                    }
                )
                claude_messages.append({"role": "user", "content": wrap_up})
            try:
                response = await _get_client().messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=claude_messages,
                )
                response_text = _clean_response_text(
                    _text_from_blocks(_blocks_to_dicts(response.content))
                )
            except Exception:  # noqa: BLE001
                logger.exception("respond: wrap-up call failed")

    if not response_text:
        response_text = (
            _clean_response_text(state.get("last_tool_result") or "")
            or "מצטער חיים, נתקלתי בבעיה טכנית. נסה לשאול שוב."
        )

    state["response"] = response_text
    state["next_action"] = "learn"

    # Persist the exchange to short-term memory (best-effort).
    session_id = state.get("session_id", "default")
    try:
        short = ShortTermMemory()
        await short.save_message(session_id, "user", state.get("user_message", ""))
        await short.save_message(session_id, "assistant", response_text)
    except Exception:  # noqa: BLE001
        logger.exception("respond: short-term memory persist failed")

    state.setdefault("messages", []).extend(
        [
            {"role": "user", "content": state.get("user_message", "")},
            {"role": "assistant", "content": response_text},
        ]
    )
    return state


async def learn_node(state: AgentState) -> AgentState:
    """Post-answer learning: store substantive exchanges + user preferences."""
    user_message = state.get("user_message", "")
    response = state.get("response") or ""

    def _persist() -> None:
        # 1) Substantive Q&A → knowledge base (used by future recalls).
        if len(response) >= 120 and not response.startswith(("⚠️", "מצטער")):
            try:
                LongTermMemory().save_knowledge(
                    f"Q: {user_message}\nA: {response[:600]}",
                    metadata={
                        "category": "agent_interaction",
                        "kind": "raw",  # purged after 7 days by the weekly job
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("learn: knowledge persist failed")

        # 2) Stable preferences Haim states in passing ("אני מעדיף...", "תזכור ש...").
        try:
            from memory.reflection_engine import ReflectionEngine, ReflectionEngineError

            try:
                engine = ReflectionEngine()
            except ReflectionEngineError:
                return
            engine.extract_user_preferences(
                [{"role": "user", "content": user_message}]
            )
        except Exception:  # noqa: BLE001
            logger.exception("learn: preference extraction failed")

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
