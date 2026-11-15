"""FastAPI application entry point."""
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .api import router
from .projects_api import router as projects_router
from .db import SessionLocal


app = FastAPI(
    title="MarsColony API",
    version="0.2.0",
    description="Authoritative game-state API for MarsColony.",
)
app.include_router(router)
app.include_router(projects_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the API process is responding."""
    return {"status": "ok"}


@app.get("/health/ready")
def readiness() -> dict[str, str]:
    """Report readiness only when the configured database accepts queries."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ok"}
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="Game storage is unavailable.",
        ) from error
