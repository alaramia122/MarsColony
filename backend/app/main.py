"""FastAPI application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import models  # noqa: F401 - register SQLAlchemy metadata
from .api import router
from .db import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Initial development bootstrap. A versioned Alembic migration is the
    # next persistence hardening task before production deployment.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="MarsColony API",
    version="0.1.0",
    description="Authoritative game-state API for MarsColony.",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
