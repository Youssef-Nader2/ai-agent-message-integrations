import time
from datetime import datetime
from typing import Callable, Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.discord.discord_auth_service import (
    DISCORD_API_BASE_URL,
    DiscordIntegrationError,
)
from app.integrations.discord.models import DiscordConnection


MAX_RATE_LIMIT_RETRY_AFTER_SECONDS = 10.0


class AttachmentMetadata(BaseModel):
    id: str
    filename: str
    url: str
    content_type: str | None
    size: int | None


class NormalizedMessage(BaseModel):
    platform: Literal["discord"]
    message_id: str
    guild_id: str
    channel_id: str
    author_id: str
    author_name: str | None
    content: str
    timestamp: datetime
    attachments: list[AttachmentMetadata]
    reply_to_message_id: str | None


def _bot_headers() -> dict[str, str]:
    if not settings.discord_bot_token:
        raise DiscordIntegrationError("Discord configuration is incomplete.", 500)
    return {"Authorization": f"Bot {settings.discord_bot_token}"}


def _retry_after(response: httpx.Response) -> float | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    value = payload.get("retry_after") if isinstance(payload, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _get_with_rate_limit_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str | int] | None,
    sleep_function: Callable[[float], None],
) -> httpx.Response:
    response = client.get(url, headers=headers, params=params)
    if response.status_code != 429:
        return response

    wait_seconds = _retry_after(response)
    if wait_seconds is None:
        raise DiscordIntegrationError("Discord rate limit response was invalid.", 503)
    if wait_seconds > MAX_RATE_LIMIT_RETRY_AFTER_SECONDS:
        raise DiscordIntegrationError("Discord rate limit exceeded.", 503)
    sleep_function(wait_seconds)
    retry_response = client.get(url, headers=headers, params=params)
    if retry_response.status_code == 429:
        raise DiscordIntegrationError("Discord rate limit exceeded.", 503)
    return retry_response


def _require_success(response: httpx.Response, resource: str) -> None:
    if response.status_code == 403:
        raise DiscordIntegrationError(f"Discord access to the {resource} was denied.", 403)
    if response.status_code == 404:
        raise DiscordIntegrationError(f"Discord {resource} was not found.", 404)
    if response.is_error:
        raise DiscordIntegrationError(f"Discord {resource} request failed.")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise DiscordIntegrationError("Discord message data was invalid.")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DiscordIntegrationError("Discord message data was invalid.") from exc
    if timestamp.tzinfo is None:
        raise DiscordIntegrationError("Discord message data was invalid.")
    return timestamp


def _normalize_attachments(value: object) -> list[AttachmentMetadata]:
    if not isinstance(value, list):
        raise DiscordIntegrationError("Discord message data was invalid.")
    attachments: list[AttachmentMetadata] = []
    for attachment in value:
        if not isinstance(attachment, dict):
            raise DiscordIntegrationError("Discord message data was invalid.")
        attachment_id = attachment.get("id")
        filename = attachment.get("filename")
        url = attachment.get("url")
        content_type = attachment.get("content_type")
        size = attachment.get("size")
        if not all(isinstance(item, str) and item for item in (attachment_id, filename, url)):
            raise DiscordIntegrationError("Discord message data was invalid.")
        if content_type is not None and not isinstance(content_type, str):
            raise DiscordIntegrationError("Discord message data was invalid.")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int)):
            raise DiscordIntegrationError("Discord message data was invalid.")
        attachments.append(
            AttachmentMetadata(
                id=attachment_id,
                filename=filename,
                url=url,
                content_type=content_type,
                size=size,
            )
        )
    return attachments


def _normalize_message(
    message: object, guild_id: str, channel_id: str
) -> NormalizedMessage:
    if not isinstance(message, dict):
        raise DiscordIntegrationError("Discord message data was invalid.")
    message_id = message.get("id")
    content = message.get("content")
    author = message.get("author")
    if not isinstance(message_id, str) or not isinstance(content, str) or not isinstance(author, dict):
        raise DiscordIntegrationError("Discord message data was invalid.")
    author_id = author.get("id")
    if not isinstance(author_id, str) or not author_id:
        raise DiscordIntegrationError("Discord message data was invalid.")
    author_name = author.get("global_name") or author.get("username")
    if author_name is not None and not isinstance(author_name, str):
        raise DiscordIntegrationError("Discord message data was invalid.")

    reference = message.get("message_reference")
    reply_to_message_id: str | None = None
    if reference is not None:
        if not isinstance(reference, dict):
            raise DiscordIntegrationError("Discord message data was invalid.")
        reference_id = reference.get("message_id")
        if reference_id is not None and not isinstance(reference_id, str):
            raise DiscordIntegrationError("Discord message data was invalid.")
        reply_to_message_id = reference_id

    return NormalizedMessage(
        platform="discord",
        message_id=message_id,
        guild_id=guild_id,
        channel_id=channel_id,
        author_id=author_id,
        author_name=author_name,
        content=content,
        timestamp=_parse_timestamp(message.get("timestamp")),
        attachments=_normalize_attachments(message.get("attachments")),
        reply_to_message_id=reply_to_message_id,
    )


def load_messages(
    session: Session,
    guild_id: str,
    channel_id: str,
    max_messages: int = 500,
    *,
    http_client: httpx.Client | None = None,
    sleep_function: Callable[[float], None] = time.sleep,
) -> list[NormalizedMessage]:
    if max_messages <= 0:
        raise DiscordIntegrationError("max_messages must be positive.", 400)
    connected = session.scalar(
        select(DiscordConnection.id)
        .where(DiscordConnection.guild_id == guild_id)
        .limit(1)
    )
    if connected is None:
        raise DiscordIntegrationError("Discord connection was not found.", 404)
    headers = _bot_headers()

    def load(client: httpx.Client) -> list[NormalizedMessage]:
        try:
            channel_response = _get_with_rate_limit_retry(
                client,
                f"{DISCORD_API_BASE_URL}/channels/{channel_id}",
                headers=headers,
                params=None,
                sleep_function=sleep_function,
            )
        except httpx.HTTPError as exc:
            raise DiscordIntegrationError("Discord channel request failed.") from exc
        _require_success(channel_response, "channel")
        try:
            channel = channel_response.json()
        except ValueError as exc:
            raise DiscordIntegrationError("Discord channel response was invalid.") from exc
        if not isinstance(channel, dict) or channel.get("guild_id") != guild_id:
            raise DiscordIntegrationError("Requested channel is outside the connected guild.", 403)

        messages: list[NormalizedMessage] = []
        before: str | None = None
        while len(messages) < max_messages:
            page_size = min(100, max_messages - len(messages))
            params: dict[str, str | int] = {"limit": page_size}
            if before is not None:
                params["before"] = before
            try:
                response = _get_with_rate_limit_retry(
                    client,
                    f"{DISCORD_API_BASE_URL}/channels/{channel_id}/messages",
                    headers=headers,
                    params=params,
                    sleep_function=sleep_function,
                )
            except httpx.HTTPError as exc:
                raise DiscordIntegrationError("Discord message request failed.") from exc
            _require_success(response, "message history")
            try:
                page = response.json()
            except ValueError as exc:
                raise DiscordIntegrationError("Discord message response was invalid.") from exc
            if not isinstance(page, list):
                raise DiscordIntegrationError("Discord message response was invalid.")
            if not page:
                break

            normalized_page = [
                _normalize_message(message, guild_id, channel_id) for message in page
            ]
            messages.extend(normalized_page)
            if len(messages) >= max_messages or len(page) < page_size:
                break
            before = normalized_page[-1].message_id
        return messages[:max_messages]

    if http_client is not None:
        return load(http_client)
    with httpx.Client(timeout=10.0) as client:
        return load(client)
