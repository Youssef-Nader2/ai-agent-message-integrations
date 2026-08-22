from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as main_module
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.integrations.discord import discord_auth
from app.integrations.discord.discord_auth_service import (
    DiscordIntegrationError,
    save_or_update_connection,
)
from app.integrations.discord.discord_loader import load_messages
from app.integrations.discord.models import DiscordConnection


def _token_data(label: str) -> dict[str, object]:
    return {
        "access_token": f"{label}-access",
        "refresh_token": f"{label}-refresh",
        "token_type": "Bearer",
        "scopes": "identify bot",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }


def _connection(discord_user_id: str, guild_id: str, label: str) -> DiscordConnection:
    return DiscordConnection(
        discord_user_id=discord_user_id,
        guild_id=guild_id,
        access_token=f"{label}-access",
        refresh_token=f"{label}-refresh",
        token_type="Bearer",
        scopes="identify bot",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture
def session(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    database_session = sessionmaker(bind=engine)()
    monkeypatch.setattr(settings, "discord_bot_token", "bot-secret")
    yield database_session
    database_session.close()
    Base.metadata.drop_all(bind=engine)


def test_two_users_can_connect_the_same_guild_without_overwriting_tokens(session):
    user_a = save_or_update_connection(
        session,
        discord_user_id="user-a",
        guild_id="guild-x",
        token_data=_token_data("user-a"),
        granted_permissions="66560",
    )
    user_b = save_or_update_connection(
        session,
        discord_user_id="user-b",
        guild_id="guild-x",
        token_data=_token_data("user-b"),
        granted_permissions="66560",
    )

    rows = session.scalars(
        select(DiscordConnection).where(DiscordConnection.guild_id == "guild-x")
    ).all()
    assert len(rows) == 2
    assert user_a.id != user_b.id
    assert {row.access_token for row in rows} == {"user-a-access", "user-b-access"}


def test_same_user_reconnecting_same_guild_updates_only_that_connection(session):
    original = save_or_update_connection(
        session,
        discord_user_id="user-a",
        guild_id="guild-x",
        token_data=_token_data("old"),
        granted_permissions="1",
    )
    updated = save_or_update_connection(
        session,
        discord_user_id="user-a",
        guild_id="guild-x",
        token_data=_token_data("new"),
        granted_permissions="66560",
    )

    assert updated.id == original.id
    rows = session.scalars(select(DiscordConnection)).all()
    assert len(rows) == 1
    assert rows[0].access_token == "new-access"
    assert rows[0].refresh_token == "new-refresh"
    assert rows[0].granted_permissions == "66560"


def test_same_user_can_connect_multiple_guilds(session):
    save_or_update_connection(
        session,
        discord_user_id="user-a",
        guild_id="guild-x",
        token_data=_token_data("guild-x"),
        granted_permissions=None,
    )
    save_or_update_connection(
        session,
        discord_user_id="user-a",
        guild_id="guild-y",
        token_data=_token_data("guild-y"),
        granted_permissions=None,
    )

    guilds = set(
        session.scalars(
            select(DiscordConnection.guild_id).where(
                DiscordConnection.discord_user_id == "user-a"
            )
        ).all()
    )
    assert guilds == {"guild-x", "guild-y"}


def test_composite_uniqueness_prevents_duplicate_user_guild_pair(session):
    session.add(_connection("user-a", "guild-x", "first"))
    session.commit()
    session.add(_connection("user-a", "guild-x", "duplicate"))

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_fresh_schema_allows_two_users_for_the_same_guild(session):
    session.add_all(
        [
            _connection("user-a", "guild-x", "first"),
            _connection("user-b", "guild-x", "second"),
        ]
    )
    session.commit()

    assert session.scalar(
        select(DiscordConnection.id).where(DiscordConnection.guild_id == "guild-x")
    ) is not None
    assert len(session.scalars(select(DiscordConnection)).all()) == 2


def test_loader_uses_bot_token_when_multiple_users_connected_to_one_guild(session):
    session.add_all(
        [
            _connection("user-a", "guild-x", "oauth-a"),
            _connection("user-b", "guild-x", "oauth-b"),
        ]
    )
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bot bot-secret"
        assert "oauth-a-access" not in request.headers["authorization"]
        assert "oauth-b-access" not in request.headers["authorization"]
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "message-1",
                        "content": "message",
                        "author": {"id": "author-1", "username": "Author"},
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "attachments": [],
                    }
                ],
            )
        return httpx.Response(200, json={"guild_id": "guild-x"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        messages = load_messages(session, "guild-x", "channel-1", http_client=client)

    assert [message.message_id for message in messages] == ["message-1"]


def test_loader_keeps_missing_guild_connection_as_404(session):
    with pytest.raises(DiscordIntegrationError) as error:
        load_messages(session, "missing-guild", "channel-1")

    assert error.value.status_code == 404


def test_callback_second_user_same_guild_preserves_first_users_connection(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    database_session = sessionmaker(bind=engine)()
    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(settings, "discord_client_id", "client-id")
    monkeypatch.setattr(settings, "discord_client_secret", "client-secret")
    monkeypatch.setattr(
        settings,
        "discord_redirect_uri",
        "http://localhost:8000/integrations/discord/callback",
    )
    monkeypatch.setattr(settings, "discord_bot_token", "bot-secret")

    save_or_update_connection(
        database_session,
        discord_user_id="user-a",
        guild_id="guild-x",
        token_data=_token_data("user-a"),
        granted_permissions="66560",
    )
    monkeypatch.setattr(
        discord_auth,
        "exchange_code_for_tokens",
        lambda _: {**_token_data("user-b"), "guild_id": "guild-x"},
    )
    monkeypatch.setattr(discord_auth, "get_current_user", lambda _: "user-b")
    monkeypatch.setattr(discord_auth, "verify_bot_guild_access", lambda _: None)

    def override_get_db():
        yield database_session

    main_module.app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(main_module.app) as client:
            authorize_response = client.get(
                "/integrations/discord/authorize", follow_redirects=False
            )
            state = authorize_response.cookies[discord_auth.STATE_COOKIE_NAME]
            response = client.get(
                "/integrations/discord/callback",
                params={"code": "synthetic-code", "state": state},
            )
        assert response.status_code == 200
        rows = database_session.scalars(
            select(DiscordConnection).where(DiscordConnection.guild_id == "guild-x")
        ).all()
        assert {row.discord_user_id for row in rows} == {"user-a", "user-b"}
        assert {row.access_token for row in rows} == {"user-a-access", "user-b-access"}
    finally:
        main_module.app.dependency_overrides.clear()
        database_session.close()
        Base.metadata.drop_all(bind=engine)
