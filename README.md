# Options Agent API

Autonomous AI options trading agent backend built with FastAPI, Motor (async MongoDB), and Pydantic v2.

## Stack

- **FastAPI** 0.111 — async Python web framework
- **Motor** 3.4 — async MongoDB driver
- **Pydantic v2** — data validation
- **MongoDB 7** — document store for positions, journal, trades
- **Docker Compose** — orchestration

## Quick start

```bash
# 1. Copy and edit env
cp .env .env.local  # then populate API keys

# 2. Build and run
docker compose up --build
```

The API will be available at:
- Root:    http://localhost:8000/
- Docs:    http://localhost:8000/docs
- Health:  http://localhost:8000/api/health
- Summary: http://localhost:8000/api/summary

MongoDB is exposed on `localhost:27017`.

## Project layout

```
options-agent/
├── docker-compose.yml
├── .env
├── .gitignore
├── README.md
└── backend/
    ├── main.py            # FastAPI app + lifespan
    ├── Dockerfile
    ├── requirements.txt
    ├── db/
    │   ├── connection.py  # Motor client + ping()
    │   └── models.py      # Pydantic v2 models
    └── api/
        └── routes.py      # /api/* endpoints
```

## Collections

| Collection  | Purpose                                       |
|-------------|-----------------------------------------------|
| `positions` | Open/closed options positions with greeks     |
| `journal`   | Daily trading journal entries                 |
| `trades`    | Individual fills (open/close/roll/adjust)     |

## Endpoints

All endpoints are mounted under `/api`.

| Method | Path                  | Description                          |
|--------|-----------------------|--------------------------------------|
| GET    | `/health`             | Liveness check                       |
| GET    | `/summary`            | Aggregate stats + last journal       |
| GET    | `/positions`          | List, filter by `status`, `ticker`   |
| GET    | `/positions/{id}`     | Single position                      |
| POST   | `/positions`          | Create position                      |
| PATCH  | `/positions/{id}`     | Update status/pnl/notes              |
| DELETE | `/positions/{id}`     | Delete position                      |
| GET    | `/journal`            | Last 30 entries                      |
| GET    | `/journal/{date}`     | Entry by `YYYY-MM-DD`                |
| POST   | `/journal`            | Create journal entry                 |
| GET    | `/trades`             | Last 50 trades, filter by `ticker`   |
| POST   | `/trades`             | Create trade                         |

## Environment variables

See `.env`. Populate API keys before running the agent loop.
