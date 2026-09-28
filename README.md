# nonbu-backend

FastAPI backend for the Nonbu fasting + food log app. Google ID tokens from the app are
exchanged for the API's own JWTs; only emails in `ALLOWED_EMAILS` may sign in.

## Local development

```sh
cp .env.example .env          # fill in JWT_SECRET, GOOGLE_CLIENT_IDS, ALLOWED_EMAILS
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 0.0.0.0
```

Swagger UI: http://localhost:8000/docs. Local dev uses SQLite by default; set
`DATABASE_URL` to a Postgres/Neon URL to use Postgres.

Tests: `uv run pytest`

New migration after changing `app/models.py`:
`uv run alembic revision --autogenerate -m "describe change"` (review the file, then commit).

## Deploy (Render + Neon, free tiers)

1. **Neon**: create a project, copy the connection string (`postgresql://…?sslmode=require`).
2. **Render**: New → Blueprint → pick this repo (uses `render.yaml`). Set env vars:
   `DATABASE_URL` (Neon), `GOOGLE_CLIENT_IDS` (Google *Web* client ID), `ALLOWED_EMAILS`.
   `JWT_SECRET` is generated automatically.
3. Every push to `main` redeploys; migrations run on container start.
4. Free Render instances sleep after ~15 idle minutes. Optional: point a free cron
   (e.g. cron-job.org) at `GET /health` every 10 minutes to keep it warm.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/google` | `{id_token}` → `{access_token, refresh_token, expires_in}` |
| POST | `/auth/refresh` | rotate refresh token (single-use) |
| POST | `/auth/logout` | revoke refresh token |
| GET/PATCH | `/me` | profile, timezone, goals |
| GET | `/fasts/current` | open fast or `null` |
| POST | `/fasts/start`, `/fasts/{id}/end` | start / end a fast |
| GET | `/fasts?from=&to=` | fasts overlapping local dates (default last 30 days) |
| PATCH/DELETE | `/fasts/{id}` | edit / delete |
| GET | `/food?date=` | entries for a local day (default today) |
| GET | `/food/recent` | latest entry per distinct food name |
| POST/GET/PATCH/DELETE | `/food`, `/food/{id}` | CRUD |
| GET | `/summary/daily?date=` | calorie/macro totals, goals, fasting hours |
| GET | `/summary/range?from=&to=` | per-day summaries (max 92 days) |
| GET | `/health` | health check |

Days are computed in the user's `timezone` (set via `PATCH /me`; the app sends the device timezone).
