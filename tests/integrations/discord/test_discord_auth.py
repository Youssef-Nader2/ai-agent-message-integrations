from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as main_module
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.integrations.discord import discord_auth


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(bind=engine)()
    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(settings, "discord_client_id", "client-id")
    monkeypatch.setattr(settings, "discord_client_secret", "client-secret")
    monkeypatch.setattr(settings, "discord_redirect_uri", "http://localhost:8000/integrations/discord/callback")
    monkeypatch.setattr(settings, "discord_bot_token", "bot-token")

    def override_get_db():
        yield test_session

    main_module.app.dependency_overrides[get_db] = override_get_db
    with TestClient(main_module.app) as test_client:
        yield test_client
    main_module.app.dependency_overrides.clear()
    test_session.close()
    Base.metadata.drop_all(bind=engine)


def _state_from_authorize(client: TestClient) -> str:
    response = client.get("/integrations/discord/authorize", follow_redirects=False)
    assert response.status_code == 302
    return response.cookies[discord_auth.STATE_COOKIE_NAME]


def test_authorize_redirects_and_sets_state_cookie(client):
    state = _state_from_authorize(client)
    response = client.get("/integrations/discord/authorize", follow_redirects=False)

    assert state
    assert response.headers["location"].startswith("https://discord.com/oauth2/authorize")
    assert "httponly" in response.headers["set-cookie"].lower()


def test_callback_success_returns_safe_metadata(client, monkeypatch):
    monkeypatch.setattr(
        discord_auth,
        "exchange_code_for_tokens",
        lambda _: {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "token_type": "Bearer",
            "scopes": "identify bot",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "guild_id": "guild-1",
        },
    )
    monkeypatch.setattr(discord_auth, "get_current_user", lambda _: "user-1")
    monkeypatch.setattr(discord_auth, "verify_bot_guild_access", lambda _: None)
    state = _state_from_authorize(client)

    response = client.get(
        "/integrations/discord/callback",
        params={"code": "code-secret", "state": state, "guild_id": "guild-1", "permissions": "66560"},
    )

    assert response.status_code == 200
    assert response.json() == {"connected": True, "discord_user_id": "user-1", "guild_id": "guild-1"}
    assert "secret" not in response.text


def test_callback_rejects_missing_or_mismatched_state(client):
    missing = client.get("/integrations/discord/callback", params={"code": "code"})
    assert missing.status_code == 400

    state = _state_from_authorize(client)
    mismatch = client.get(
        "/integrations/discord/callback", params={"code": "code", "state": f"{state}-other"}
    )
    assert mismatch.status_code == 400


def test_callback_processes_provider_error_only_after_valid_state(client):
    state = _state_from_authorize(client)
    denied = client.get(
        "/integrations/discord/callback",
        params={"state": state, "error": "access_denied"},
    )
    assert denied.status_code == 400
    assert denied.json()["detail"] == "Discord authorization was denied."
    assert "max-age=0" in denied.headers["set-cookie"].lower()

    state = _state_from_authorize(client)
    missing_code = client.get("/integrations/discord/callback", params={"state": state})
    assert missing_code.status_code == 400


def test_callback_validates_state_before_provider_error(client):
    missing_state = client.get(
        "/integrations/discord/callback", params={"error": "access_denied"}
    )
    assert missing_state.status_code == 400
    assert missing_state.json()["detail"] == "Discord OAuth state is missing."

    state = _state_from_authorize(client)
    mismatched_state = client.get(
        "/integrations/discord/callback",
        params={"state": f"{state}-other", "error": "access_denied"},
    )
    assert mismatched_state.status_code == 400
    assert mismatched_state.json()["detail"] == "Discord OAuth state is invalid."
    assert "set-cookie" not in mismatched_state.headers
    assert client.cookies.get(discord_auth.STATE_COOKIE_NAME) == state


def test_callback_rejects_conflicting_guild_ids(client, monkeypatch):
    monkeypatch.setattr(
        discord_auth,
        "exchange_code_for_tokens",
        lambda _: {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "token_type": "Bearer",
            "scopes": "identify bot",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "guild_id": "guild-from-token",
        },
    )
    state = _state_from_authorize(client)

    response = client.get(
        "/integrations/discord/callback",
        params={"code": "code", "state": state, "guild_id": "guild-from-callback"},
    )

    assert response.status_code == 400


def test_callback_uses_token_guild_when_callback_guild_is_absent(client, monkeypatch):
    monkeypatch.setattr(
        discord_auth,
        "exchange_code_for_tokens",
        lambda _: {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "token_type": "Bearer",
            "scopes": "identify bot",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "guild_id": "guild-from-token",
        },
    )
    monkeypatch.setattr(discord_auth, "get_current_user", lambda _: "user-1")
    monkeypatch.setattr(discord_auth, "verify_bot_guild_access", lambda _: None)
    state = _state_from_authorize(client)

    response = client.get(
        "/integrations/discord/callback", params={"code": "code", "state": state}
    )

    assert response.status_code == 200
    assert response.json()["guild_id"] == "guild-from-token"


def test_callback_rejects_callback_guild_when_token_guild_is_missing(client, monkeypatch):
    verification_called = False

    monkeypatch.setattr(
        discord_auth,
        "exchange_code_for_tokens",
        lambda _: {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "token_type": "Bearer",
            "scopes": "identify bot",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "guild_id": None,
        },
    )

    def verify(_: str) -> None:
        nonlocal verification_called
        verification_called = True

    monkeypatch.setattr(discord_auth, "verify_bot_guild_access", verify)
    state = _state_from_authorize(client)

    response = client.get(
        "/integrations/discord/callback",
        params={"code": "code", "state": state, "guild_id": "guild-from-callback"},
    )

    assert response.status_code == 400
    assert verification_called is False
