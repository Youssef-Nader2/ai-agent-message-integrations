from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.integrations.discord import models as discord_models
from app.integrations.discord.discord_auth import router as discord_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.application_name, lifespan=lifespan)
app.include_router(discord_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
