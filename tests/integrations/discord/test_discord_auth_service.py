from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.integrations.discord.discord_auth_service import (
    DISCORD_BOT_PERMISSIONS,
    DiscordIntegrationError,
    build_authorization_url,
    exchange_code_for_tokens,
    get_current_user,
    refresh_access_token,
    verify_bot_guild_access,
)
from app.integrations.discord.models import DiscordConnection


@pytest.fixture
def configured_discord(monkeypatch):
    monkeypatch.setattr(settings, "discord_client_id", "client-id")
    monkeypatch.setattr(settings, "discord_client_secret", "client-secret")
    monkeypatch.setattr(settings, "discord_redirect_uri", "http://localhost:8000/integrations/discord/callback")
    monkeypatch.setattr(settings, "discord_bot_token", "bot-token")


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    database_session = sessionmaker(bind=engine)()
    yield database_session
    database_session.close()
    Base.metadata.drop_all(bind=engine)


def test_build_authorization_url_uses_approved_scopes_and_permissions(configured_discord):
    parsed = urlparse(build_authorization_url("state-value"))
    query = parse_qs(parsed.query)

    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/integrations/discord/callback"]
    assert query["state"] == ["state-value"]
    assert query["scope"] == ["identify bot"]
    assert query["permissions"] == [str(DISCORD_BOT_PERMISSIONS)]


def test_exchange_code_parses_tokens_and_extended_guild(configured_discord):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/oauth2/token")
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        return httpx.Response(
            200,
            json={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "identify bot",
                "guild": {"id": "guild-1"},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token_data = exchange_code_for_tokens("code", client)

    assert token_data["guild_id"] == "guild-1"
    assert token_data["refresh_token"] == "refresh-token"
    assert token_data["expires_at"] > datetime.now(timezone.utc)


def test_exchange_code_rejects_malformed_response_without_leaking_token(configured_discord):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"access_token": "do-not-leak", "token_type": "Bearer", "expires_in": 60},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DiscordIntegrationError) as error:
            exchange_code_for_tokens("code", client)

    assert "do-not-leak" not in str(error.value)


def test_get_current_user_returns_discord_snowflake(configured_discord):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer access-token"
        return httpx.Response(200, json={"id": "user-1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert get_current_user("access-token", client) == "user-1"


def test_refresh_access_token_updates_connection(configured_discord, session):
    connection = DiscordConnection(
        discord_user_id="user-1",
        guild_id="guild-1",
        access_token="old-access",
        refresh_token="old-refresh",
        token_type="Bearer",
        scopes="identify bot",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    session.add(connection)
    session.commit()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "identify bot",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        refreshed = refresh_access_token(session, connection, client)

    assert refreshed.access_token == "new-access"
    assert refreshed.refresh_token == "new-refresh"
    assert refreshed.expires_at > datetime.now(timezone.utc)


def test_verify_bot_guild_access(configured_discord):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bot bot-token"
        return httpx.Response(200, json={"id": "guild-1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        verify_bot_guild_access("guild-1", client)
