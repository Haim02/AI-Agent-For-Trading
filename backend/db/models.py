from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Strategy = Literal[
    "iron_condor",
    "short_strangle",
    "bull_put_spread",
    "bear_call_spread",
    "long_straddle",
    "calendar_spread",
    "other",
]

PositionStatus = Literal["open", "closed", "expired"]
TradeAction = Literal["open", "close", "roll", "adjust"]
OptionType = Literal["call", "put", "stock"]


class Greeks(BaseModel):
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None


class Position(BaseModel):
    ticker: str
    strategy: Strategy
    status: PositionStatus = "open"
    legs: list[dict[str, Any]] = Field(default_factory=list)
    premium_received: Optional[float] = None
    premium_paid: Optional[float] = None
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None
    greeks_at_entry: Optional[Greeks] = None
    entry_date: datetime = Field(default_factory=datetime.utcnow)
    expiration_date: Optional[datetime] = None
    close_date: Optional[datetime] = None
    realized_pnl: Optional[float] = None
    vix_at_entry: Optional[float] = None
    gex_regime_at_entry: Optional[str] = None
    agent_reasoning: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PositionCreate(BaseModel):
    ticker: str
    strategy: Strategy
    legs: list[dict[str, Any]] = Field(default_factory=list)
    premium_received: Optional[float] = None
    premium_paid: Optional[float] = None
    expiration_date: Optional[datetime] = None
    vix_at_entry: Optional[float] = None
    notes: Optional[str] = None


class PositionUpdate(BaseModel):
    status: Optional[PositionStatus] = None
    realized_pnl: Optional[float] = None
    close_date: Optional[datetime] = None
    notes: Optional[str] = None


class JournalEntry(BaseModel):
    date: str
    daily_pnl: float = 0.0
    weekly_pnl: Optional[float] = None
    trades_count: int = 0
    vix_open: Optional[float] = None
    vix_close: Optional[float] = None
    spx_change_pct: Optional[float] = None
    gex_regime: Optional[str] = None
    gamma_flip_level: Optional[float] = None
    agent_summary: Optional[str] = None
    lessons_learned: Optional[str] = None
    next_day_watchlist: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class JournalCreate(BaseModel):
    date: str
    daily_pnl: float = 0.0
    notes: Optional[str] = None


class Trade(BaseModel):
    position_id: Optional[str] = None
    ticker: str
    action: TradeAction
    option_type: OptionType
    strike: Optional[float] = None
    expiry: Optional[datetime] = None
    quantity: int = 1
    price: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TradeCreate(BaseModel):
    position_id: Optional[str] = None
    ticker: str
    action: TradeAction
    option_type: OptionType
    strike: Optional[float] = None
    expiry: Optional[datetime] = None
    quantity: int = 1
    price: float = 0.0
    reason: Optional[str] = None


# ───────────────────────── conversations ─────────────────────────

ChatRole = Literal["user", "assistant"]


def _expiry_30d() -> datetime:
    return datetime.utcnow() + timedelta(days=30)


class ConversationMessage(BaseModel):
    session_id: str
    role: ChatRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationSession(BaseModel):
    session_id: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_active: datetime = Field(default_factory=datetime.utcnow)
    message_count: int = 0
    topics: list[str] = Field(default_factory=list)
    expires_at: datetime = Field(default_factory=_expiry_30d)


# ───────────────────────── news monitor ─────────────────────────

NewsCategory = Literal[
    "earnings",
    "analyst",
    "merger",
    "geopolitical",
    "fed",
    "economic",
    "company_news",
    "macro",
]
NewsImportance = Literal["high", "medium", "low"]
NewsSentiment = Literal["bullish", "bearish", "neutral"]


def _expiry_7d() -> datetime:
    return datetime.utcnow() + timedelta(days=7)


class NewsItem(BaseModel):
    """News item with deduplication and impact tracking."""

    news_hash: str
    headline: str
    summary: Optional[str] = None
    source: str
    url: Optional[str] = None

    category: NewsCategory
    importance: NewsImportance
    sentiment: Optional[NewsSentiment] = None

    tickers: list[str] = Field(default_factory=list)

    published_at: datetime
    sent_to_user_at: Optional[datetime] = None

    price_at_news: dict[str, float] = Field(default_factory=dict)
    price_after_1d: dict[str, float] = Field(default_factory=dict)
    price_after_3d: dict[str, float] = Field(default_factory=dict)
    price_after_7d: dict[str, float] = Field(default_factory=dict)

    impact_score: Optional[float] = None

    agent_analysis: Optional[str] = None
    confirmed_impact: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(default_factory=_expiry_7d)
