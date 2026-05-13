"""Compiles the LangGraph state machine for the autonomous trading agent."""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    act_node,
    learn_node,
    observe_node,
    perceive_node,
    respond_node,
    think_node,
)
from agent.state import AgentState, MAX_ITERATIONS

logger = logging.getLogger(__name__)


def _route_after_think(state: AgentState) -> str:
    if state.get("next_action") == "respond":
        return "respond"
    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        return "respond"
    return "act"


def _route_after_observe(state: AgentState) -> str:
    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        return "respond"
    if state.get("next_action") == "respond":
        return "respond"
    return "think"


def create_agent_graph():
    """Build and compile the LangGraph state machine."""
    graph = StateGraph(AgentState)

    graph.add_node("perceive", perceive_node)
    graph.add_node("think", think_node)
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)
    graph.add_node("respond", respond_node)
    graph.add_node("learn", learn_node)

    graph.add_edge(START, "perceive")
    graph.add_edge("perceive", "think")

    graph.add_conditional_edges(
        "think",
        _route_after_think,
        {"act": "act", "respond": "respond"},
    )
    graph.add_edge("act", "observe")
    graph.add_conditional_edges(
        "observe",
        _route_after_observe,
        {"think": "think", "respond": "respond"},
    )
    graph.add_edge("respond", "learn")
    graph.add_edge("learn", END)

    compiled = graph.compile()
    logger.info("LangGraph agent compiled")
    return compiled


__all__ = ["create_agent_graph"]
