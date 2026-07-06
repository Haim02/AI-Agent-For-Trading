# Options Agent – סוכן AI אישי למסחר ב-SPX 0DTE

Autonomous AI trading assistant for Haim: SPX 0DTE options with GEX/DEX levels,
news monitoring, a learning memory, a Telegram bot, and a React dashboard.

## What it does

- **16:30 Israel (US market open)** – market-open alert + today's SPX GEX levels
  (Call Wall / Put Wall / Gamma Flip / 0DTE Magnet) straight to Telegram.
- **Intraday** – DEX (Delta Exposure) monitor alerts when a new Delta
  support/resistance wall forms near SPX spot; Wall-break detector watches
  Call/Put Wall and Gamma-Flip crosses every 5 minutes.
- **News** – smart news monitor (Finnhub + Perplexity) with dedup, Hebrew
  translation, impact tracking, and self-learning of news→price patterns.
- **Agent brain** – LangGraph perceive→think→act→respond→learn loop over Claude
  with 30+ tools (GEX, flow, IV rank, earnings, macro, memory, Telegram).
- **Memory** – short-term (MongoDB conversations) + long-term semantic memory
  (ChromaDB) + nightly reflection engine that distills lessons from each day.

## Stack

| Layer     | Tech |
|-----------|------|
| API       | FastAPI + Uvicorn |
| Agent     | LangGraph + Anthropic Claude (`ANTHROPIC_MODEL`, default `claude-sonnet-5`) |
| DB        | MongoDB (Motor) – positions, journal, trades, news, GEX/DEX history |
| Memory    | ChromaDB + sentence-transformers |
| Data      | FlashAlpha (primary GEX/DEX) → Unusual Whales → Massive → yfinance |
| Messaging | python-telegram-bot (polling) |
| Frontend  | React + Vite + TypeScript (dashboard, GEX chart, journal, chat) |

## Quick start

```bash
cp .env.example .env   # fill in keys – MONGO_URL is required
docker compose up --build
```

- API: http://localhost:8000/docs
- Frontend dev: `cd frontend && npm install && npm run dev`

## Telegram commands

| Command | Description |
|---------|-------------|
| `/gex [TICKER]` | ניתוח GEX מלא (ברירת מחדל SPX) |
| `/dex [TICKER]` | תמיכות/התנגדויות Delta |
| `/levels`, `/narrative`, `/signals`, `/whales` | FlashAlpha analytics |
| `/scan`, `/positions`, `/summary`, `/learn` | agent ops |
| טקסט חופשי בעברית | שיחה עם הסוכן (עם זיכרון) |

## Key schedules (Israel time)

| Time | Job |
|------|-----|
| 08:00 | Morning briefing (macro + Fear&Greed + calendar) |
| 14:00 | Pre-market scan + GEX/Flow reports (SPY/QQQ/SPX) |
| 16:30 | **Market open alert + SPX GEX levels for 0DTE** |
| Every 5 min (session) | Wall-break detector (SPY) |
| Every 30 min (session) | DEX support/resistance monitor (SPX), GEX snapshot, news |
| 23:30 | EOD summary + reflection (learning) |
| Sunday 08:00 | Weekly P&L report |

## Backend layout

```
backend/
├── main.py               # FastAPI app + lifespan (bot, scheduler, monitors)
├── agent/                # LangGraph state machine + 30+ tools
├── analytics/            # GEX engine, flow engine, IV rank, strategy selector
├── api/                  # REST routes (positions, journal, GEX chart, analytics)
├── db/                   # Motor connection + Pydantic models
├── memory/               # short/long-term memory, reflection, GEX knowledge RAG
├── scrapers/             # FinViz, MenthorQ (Selenium), Unusual Whales (Playwright)
├── services/             # scheduler, telegram bot, news, DEX monitor, scanners
└── tools/                # FlashAlpha, Massive, Finnhub, Perplexity, macro, ...
```

## Notes

- Chart candles fall back to synthetic data when Yahoo is unreachable — the
  response then carries `synthetic_candles: true` and the session label shows
  "⚠️ סימולציה" so fake candles are never mistaken for live data.
- MenthorQ scraping is kept only behind `/api/gex/menthorq`; all scheduled jobs
  use the FlashAlpha-first `GEXEngine`.
