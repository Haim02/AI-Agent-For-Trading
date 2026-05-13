"""CNN Fear & Greed Index – sentiment-driven IV signal."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _hebrew_rating(score: float) -> tuple[str, str, str]:
    """Returns (hebrew_rating, strategy_implication, color)."""
    if score <= 25:
        return (
            "פחד קיצוני 😱",
            "זמן מעולה למכור אופציות! IV גבוה",
            "red",
        )
    if score <= 45:
        return (
            "פחד 😨",
            "IV מוגבה – כדאי לשקול מכירת פרמיה",
            "orange",
        )
    if score <= 55:
        return ("ניטרלי 😐", "שוק מאוזן", "yellow")
    if score <= 75:
        return (
            "חמדנות 😊",
            "IV נמוך – זהירות במכירת פרמיה",
            "green",
        )
    return (
        "חמדנות קיצונית 🤑",
        "IV נמוך מאוד – עדיף Debit Spreads",
        "green",
    )


class FearGreedTool:
    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def get_fear_greed_index(self) -> dict[str, Any]:
        try:
            response = requests.get(CNN_URL, headers=HEADERS, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fear & Greed fetch failed: %s", exc)
            return {}

        try:
            block = data["fear_and_greed"]
            score = float(block.get("score") or 0.0)
            rating = block.get("rating", "")
        except (KeyError, TypeError, ValueError):
            logger.warning("Fear & Greed payload malformed: %s", str(data)[:200])
            return {}

        hebrew_rating, implication, color = _hebrew_rating(score)
        return {
            "score": round(score, 1),
            "rating": rating,
            "hebrew_rating": hebrew_rating,
            "strategy_implication": implication,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def get_historical_scores(self, days: int = 7) -> list[dict[str, Any]]:
        try:
            response = requests.get(CNN_URL, headers=HEADERS, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fear & Greed history fetch failed: %s", exc)
            return []

        series = data.get("fear_and_greed_historical", {}).get("data", []) or []
        out: list[dict[str, Any]] = []
        for point in series[-days:]:
            try:
                ts = int(point.get("x") or 0) / 1000.0
                score = float(point.get("y") or 0.0)
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "date": datetime.utcfromtimestamp(ts).date().isoformat(),
                    "score": round(score, 1),
                    "rating": point.get("rating", ""),
                }
            )
        return out


__all__ = ["FearGreedTool"]
