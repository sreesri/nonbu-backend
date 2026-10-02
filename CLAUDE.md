# CLAUDE.md

Project-wide context (architecture, auth flow, timezones, deploy) is in the parent
directory's `CLAUDE.md`. This file holds the coding rules for this repo.

## Code standards

All code must meet industry-standard production quality. Concretely:

**General**
- Read the surrounding code first and match its structure, naming, and idioms; don't introduce a second way of doing something the codebase already does one way.
- Small, single-purpose functions and components; no dead code, commented-out code, or leftover debug logging.
- Descriptive names; no magic numbers or strings — lift them into named constants.
- Handle errors explicitly at boundaries (network, DB, user input); never silently swallow them unless it's intentional and commented (e.g. best-effort notification scheduling).
- Comments explain *why*, not *what*. Public helpers and non-obvious logic get a short docstring.
- Never commit secrets; config comes from env vars (`.env` / `.env.local` are git-ignored).
- Don't add a dependency when the platform or an existing dependency already covers it.
- A task is done only when the project's checks pass (below) — report failures, never hide them.

**Backend (Python / FastAPI)**
- PEP 8 style; full type hints on every function signature (params and return).
- Request/response bodies are Pydantic models in `schemas.py`; never return ORM objects or raw dicts from routes.
- Async all the way: async SQLAlchemy sessions, no blocking I/O in request handlers.
- Every query is scoped to `get_current_user`; never trust client-supplied user IDs.
- Use proper HTTP status codes via `HTTPException` (400/401/403/404/409/422), not 200 with an error payload.
- Any model change ships with a reviewed Alembic migration.
- New or changed behaviour gets pytest coverage in `tests/` (happy path + at least one failure/edge case, incl. timezone/DST edges for date logic).
- Checks (also run in CI, `.github/workflows/ci.yml`) — all must pass:
  `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
  Fix lint/type errors properly; a `# noqa` or `# type: ignore` needs the specific code and a reason.
