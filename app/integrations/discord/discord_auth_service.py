from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.discord.models import DiscordConnection


DISCORD_API_BASE_URL = "https://discord.com/api/v10"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_BOT_PERMISSIONS = 66560
DISCORD_OAUTH_SCOPES = "identify bot"


class DiscordIntegrationError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _require_oauth_configuration() -> tuple[str, str, str]:
    client_id = settings.discord_client_id
    client_secret = settings.discord_client_secret
    redirect_uri = settings.discord_redirect_uri
    if not all((client_id, client_secret, redirect_uri)):
        raise DiscordIntegrationError("Discord configuration is incomplete.", 500)
    return client_id, client_secret, redirect_uri


def _require_bot_token() -> str:
    if not settings.discord_bot_token:
        raise DiscordIntegrationError("Discord configuration is incomplete.", 500)
    return settings.discord_bot_token


def _request_token(
    data: dict[str, str], http_client: httpx.Client | None = None
) -> dict[str, Any]:
    client_id, client_secret, _ = _require_oauth_configuration()

    def send(client: httpx.Client) -> httpx.Response:
        return client.post(
            f"{DISCORD_API_BASE_URL}/oauth2/token",
            data=data,
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    try:
        if http_client is not None:
            response = send(http_client)
        else:
            with httpx.Client(timeout=10.0) as client:
                response = send(client)
    except httpx.HTTPError as exc:
        raise DiscordIntegrationError("Discord token exchange failed.") from exc

    if response.is_error:
        raise DiscordIntegrationError("Discord token exchange failed.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise DiscordIntegrationError("Discord token response was invalid.") from exc

    if not isinstance(payload, dict):
        raise DiscordIntegrationError("Discord token response was invalid.")
    return payload


def _parse_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    access_token = payload.get("access_token")
    token_type = payload.get("token_type")
    expires_in = payload.get("expires_in")
    scopes = payload.get("scope")
    if not all(isinstance(value, str) and value for value in (access_token, token_type, scopes)):
        raise DiscordIntegrationError("Discord token response was invalid.")
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
        raise DiscordIntegrationError("Discord token response was invalid.")
    if expires_in <= 0:
        raise DiscordIntegrationError("Discord token response was invalid.")

    refresh_token = payload.get("refresh_token")
    if refresh_token is not None and not isinstance(refresh_token, str):
        raise DiscordIntegrationError("Discord token response was invalid.")

    guild_id: str | None = None
    guild = payload.get("guild")
    if guild is not None:
        if not isinstance(guild, dict) or not isinstance(guild.get("id"), str):
            raise DiscordIntegrationError("Discord token response was invalid.")
        guild_id = guild["id"]

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": token_type,
        "scopes": scopes,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        "guild_id": guild_id,
    }


def build_authorization_url(state: str) -> str:
    if not state:
        raise DiscordIntegrationError("OAuth state is required.", 400)
    client_id, _, redirect_uri = _require_oauth_configuration()
    parameters = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": DISCORD_OAUTH_SCOPES,
        "permissions": str(DISCORD_BOT_PERMISSIONS),
        "state": state,
    }
    return f"{DISCORD_AUTHORIZE_URL}?{urlencode(parameters)}"


def exchange_code_for_tokens(
    code: str, http_client: httpx.Client | None = None
) -> dict[str, Any]:
    if not code:
        raise DiscordIntegrationError("Discord authorization code is required.", 400)
    _, _, redirect_uri = _require_oauth_configuration()
    payload = _request_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        http_client,
    )
    return _parse_token_payload(payload)


def refresh_access_token(
    session: Session,
    connection: DiscordConnection,
    http_client: httpx.Client | None = None,
) -> DiscordConnection:
    if not connection.refresh_token:
        raise DiscordIntegrationError("Discord token refresh is unavailable.")
    payload = _request_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": connection.refresh_token,
        },
        http_client,
    )
    token_data = _parse_token_payload(payload)
    connection.access_token = token_data["access_token"]
    connection.refresh_token = token_data["refresh_token"] or connection.refresh_token
    connection.token_type = token_data["token_type"]
    connection.scopes = token_data["scopes"]
    connection.expires_at = token_data["expires_at"]
    connection.updated_at = datetime.now(timezone.utc)
    try:
        session.commit()
        session.refresh(connection)
    except SQLAlchemyError as exc:
        session.rollback()
        raise DiscordIntegrationError("Unable to save Discord connection.", 500) from exc
    return connection


def get_current_user(
    access_token: str, http_client: httpx.Client | None = None
) -> str:
    if not access_token:
        raise DiscordIntegrationError("Discord authorization failed.", 400)

    def send(client: httpx.Client) -> httpx.Response:
        return client.get(
            f"{DISCORD_API_BASE_URL}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    try:
        if http_client is not None:
            response = send(http_client)
        else:
            with httpx.Client(timeout=10.0) as client:
                response = send(client)
    except httpx.HTTPError as exc:
        raise DiscordIntegrationError("Discord user lookup failed.") from exc
    if response.is_error:
        raise DiscordIntegrationError("Discord user lookup failed.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise DiscordIntegrationError("Discord user response was invalid.") from exc
    user_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(user_id, str) or not user_id:
        raise DiscordIntegrationError("Discord user response was invalid.")
    return user_id


def verify_bot_guild_access(
    guild_id: str, http_client: httpx.Client | None = None
) -> None:
    if not guild_id:
        raise DiscordIntegrationError("Discord guild is required.", 400)
    bot_token = _require_bot_token()

    def send(client: httpx.Client) -> httpx.Response:
        return client.get(
            f"{DISCORD_API_BASE_URL}/guilds/{guild_id}",
            headers={"Authorization": f"Bot {bot_token}"},
        )

    try:
        if http_client is not None:
            response = send(http_client)
        else:
            with httpx.Client(timeout=10.0) as client:
                response = send(client)
    except httpx.HTTPError as exc:
        raise DiscordIntegrationError("Discord guild verification failed.") from exc
    if response.status_code in (401, 403, 404):
        raise DiscordIntegrationError("Discord bot cannot access the selected guild.", 403)
    if response.is_error:
        raise DiscordIntegrationError("Discord guild verification failed.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise DiscordIntegrationError("Discord guild response was invalid.") from exc
    if not isinstance(payload, dict) or payload.get("id") != guild_id:
        raise DiscordIntegrationError("Discord bot cannot access the selected guild.", 403)


def save_or_update_connection(
    session: Session,
    *,
    discord_user_id: str,
    guild_id: str,
    token_data: dict[str, Any],
    granted_permissions: str | None,
) -> DiscordConnection:
    connection = session.scalar(
        select(DiscordConnection).where(DiscordConnection.guild_id == guild_id)
    )
    if connection is None:
        connection = DiscordConnection(
            discord_user_id=discord_user_id,
            guild_id=guild_id,
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            token_type=token_data["token_type"],
            scopes=token_data["scopes"],
            expires_at=token_data["expires_at"],
            granted_permissions=granted_permissions,
        )
        session.add(connection)
    else:
        connection.discord_user_id = discord_user_id
        connection.access_token = token_data["access_token"]
        connection.refresh_token = token_data["refresh_token"]
        connection.token_type = token_data["token_type"]
        connection.scopes = token_data["scopes"]
        connection.expires_at = token_data["expires_at"]
        connection.granted_permissions = granted_permissions
        connection.updated_at = datetime.now(timezone.utc)
    try:
        session.commit()
        session.refresh(connection)
    except SQLAlchemyError as exc:
        session.rollback()
        raise DiscordIntegrationError("Unable to save Discord connection.", 500) from exc
    return connection
