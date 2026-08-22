from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import engine, get_db
from app.integrations.discord.discord_auth_service import DiscordIntegrationError
from app.integrations.discord.discord_loader import load_messages
from app.integrations.discord.message_cleaner import clean_message


PREVIEW_TEXT_LIMIT = 2000
MODULE_DIR = Path(__file__).parent
TEMPLATES_DIR = MODULE_DIR / "templates"
STATIC_DIR = MODULE_DIR / "static"

router = APIRouter(prefix="/integrations/discord", tags=["discord-ui"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class DiscordPreviewRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    guild_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    max_messages: int = Field(default=10, ge=1, le=100)


class DiscordMessagePreview(BaseModel):
    message_id: str
    author_id: str
    author_name: str | None
    timestamp: datetime
    original_content: str
    cleaned_content: str
    original_truncated: bool
    cleaned_truncated: bool
    attachment_count: int
    reply_to_message_id: str | None


class DiscordPreviewResponse(BaseModel):
    messages: list[DiscordMessagePreview]


def _preview_text(content: str) -> tuple[str, bool]:
    if len(content) <= PREVIEW_TEXT_LIMIT:
        return content, False
    return content[:PREVIEW_TEXT_LIMIT], True


def _safe_error_detail(error: DiscordIntegrationError) -> str:
    messages = {
        400: "Invalid Discord preview request.",
        403: "Discord access was denied.",
        404: "Discord connection or channel was not found.",
        500: "Discord configuration is incomplete.",
        503: "Discord is temporarily rate limited or unavailable.",
    }
    return messages.get(error.status_code, "Discord message preview could not be loaded.")


@router.get("/ui", response_class=HTMLResponse)
def discord_console(request: Request) -> HTMLResponse:
    oauth_configured = all(
        (
            settings.discord_client_id,
            settings.discord_client_secret,
            settings.discord_redirect_uri,
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="discord_console.html",
        context={
            "bot_token_configured": bool(settings.discord_bot_token),
            "oauth_configured": oauth_configured,
            "database_backend": engine.dialect.name,
        },
    )


@router.post("/ui/load", response_model=DiscordPreviewResponse)
def load_discord_preview(
    preview_request: DiscordPreviewRequest,
    session: Session = Depends(get_db),
) -> DiscordPreviewResponse:
    try:
        messages = load_messages(
            session,
            preview_request.guild_id,
            preview_request.channel_id,
            preview_request.max_messages,
        )
    except DiscordIntegrationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=_safe_error_detail(exc),
        ) from exc

    previews: list[DiscordMessagePreview] = []
    for message in messages:
        original_content, original_truncated = _preview_text(message.content)
        cleaned_content, cleaned_truncated = _preview_text(clean_message(message.content))
        previews.append(
            DiscordMessagePreview(
                message_id=message.message_id,
                author_id=message.author_id,
                author_name=message.author_name,
                timestamp=message.timestamp,
                original_content=original_content,
                cleaned_content=cleaned_content,
                original_truncated=original_truncated,
                cleaned_truncated=cleaned_truncated,
                attachment_count=len(message.attachments),
                reply_to_message_id=message.reply_to_message_id,
            )
        )
    return DiscordPreviewResponse(messages=previews)
