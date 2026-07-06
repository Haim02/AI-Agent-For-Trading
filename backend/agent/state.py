"""Shared state schema for the LangGraph autonomous agent."""

from __future__ import annotations

from typing import Any, Optional, TypedDict


# Each iteration = one Claude call. 6 keeps latency/cost sane while still
# allowing multi-step research (e.g. GEX → flow → IV → answer).
MAX_ITERATIONS = 6


class AgentState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    session_id: str
    user_message: str

    current_ticker: Optional[str]
    current_strategy: Optional[str]

    market_context: dict[str, Any]
    memory_context: str  # rich Hebrew context block from ContextBuilder

    # Native Anthropic tool-use conversation thread (assistant/tool_result turns)
    claude_messages: list[dict[str, Any]]
    pending_tools: list[dict[str, Any]]

    scan_results: Optional[list[dict[str, Any]]]
    analysis_results: Optional[dict[str, Any]]
    strategy_params: Optional[dict[str, Any]]

    decision: Optional[str]
    response: Optional[str]
    next_action: str
    tool_input: Any
    last_tool_result: Optional[str]

    iteration_count: int
    errors: list[str]


def initial_state(user_message: str, session_id: str = "default") -> AgentState:
    return AgentState(
        messages=[],
        session_id=session_id,
        user_message=user_message,
        current_ticker=None,
        current_strategy=None,
        market_context={},
        memory_context="",
        claude_messages=[],
        pending_tools=[],
        scan_results=None,
        analysis_results=None,
        strategy_params=None,
        decision=None,
        response=None,
        next_action="think",
        tool_input=None,
        last_tool_result=None,
        iteration_count=0,
        errors=[],
    )


__all__ = ["AgentState", "MAX_ITERATIONS", "initial_state"]
