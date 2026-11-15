"""FastAPI application entry point."""
from fastapi import FastAPI

from .api import router


app = FastAPI(
    title="MarsColony API",
    version="0.1.0",
    description="Authoritative game-state API for MarsColony.",
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
