from fastapi import FastAPI

from app.routers import auth, fasts, food, me, summary

app = FastAPI(title="Nonbu API", version="0.1.0")

for module in (auth, me, fasts, food, summary):
    app.include_router(module.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
