from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as main_module
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.integrations.discord import discord_ui
from app.integrations.discord.discord_auth_service import DiscordIntegrationError
from app.integrations.discord.discord_loader import NormalizedMessage


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
    monkeypatch.setattr(
        settings,
        "discord_redirect_uri",
        "http://localhost:8000/integrations/discord/callback",
    )
    monkeypatch.setattr(settings, "discord_bot_token", "bot-secret")

    def override_get_db():
        yield test_session

    main_module.app.dependency_overrides[get_db] = override_get_db
    with TestClient(main_module.app) as test_client:
        yield test_client
    main_module.app.dependency_overrides.clear()
    test_session.close()
    Base.metadata.drop_all(bind=engine)


def _message(content: str = "btw 😀") -> NormalizedMessage:
    return NormalizedMessage(
        platform="discord",
        message_id="message-1",
        guild_id="guild-1",
        channel_id="channel-1",
        author_id="author-1",
        author_name="Author",
        content=content,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        attachments=[],
        reply_to_message_id=None,
    )


def test_console_renders_controls_safe_status_and_no_secrets(client):
    response = client.get("/integrations/discord/ui")

    assert response.status_code == 200
    assert "Discord Integration Console" in response.text
    assert "Connect Discord" in response.text
    assert 'id="guild-id"' in response.text
    assert 'id="channel-id"' in response.text
    assert 'id="max-messages"' in response.text
    assert "READ-ONLY" in response.text
    assert "bot-secret" not in response.text
    assert "client-secret" not in response.text


def test_preview_reuses_loader_and_cleaner_for_safe_message_preview(client, monkeypatch):
    calls = []

    def load(session, guild_id, channel_id, max_messages):
        calls.append((session, guild_id, channel_id, max_messages))
        return [_message()]

    monkeypatch.setattr(discord_ui, "load_messages", load)
    response = client.post(
        "/integrations/discord/ui/load",
        json={"guild_id": "guild-1", "channel_id": "channel-1", "max_messages": 10},
    )

    assert response.status_code == 200
    assert calls[0][1:] == ("guild-1", "channel-1", 10)
    preview = response.json()["messages"][0]
    assert preview["original_content"] == "btw 😀"
    assert preview["cleaned_content"] == "by the way grinning face"
    assert preview["attachment_count"] == 0
    assert "bot-secret" not in response.text


@pytest.mark.parametrize("max_messages", [0, 101])
def test_preview_rejects_out_of_range_max_messages(client, max_messages):
    response = client.post(
        "/integrations/discord/ui/load",
        json={"guild_id": "guild-1", "channel_id": "channel-1", "max_messages": max_messages},
    )

    assert response.status_code == 422


def test_preview_maps_integration_error_to_safe_client_error(client, monkeypatch):
    def fail(*_):
        raise DiscordIntegrationError("raw internal bot-secret error", 503)

    monkeypatch.setattr(discord_ui, "load_messages", fail)
    response = client.post(
        "/integrations/discord/ui/load",
        json={"guild_id": "guild-1", "channel_id": "channel-1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Discord is temporarily rate limited or unavailable."
    assert "bot-secret" not in response.text


def test_gui_introduces_only_read_endpoint_and_preserves_auth_routes():
    paths = main_module.app.openapi()["paths"]

    assert set(paths["/integrations/discord/ui"]) == {"get"}
    assert set(paths["/integrations/discord/ui/load"]) == {"post"}
    assert "/integrations/discord/authorize" in paths
    assert "/integrations/discord/callback" in paths
