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
from app.integrations.discord.discord_loader import (
    ConnectedGuild,
    GuildMessageLoadResult,
    NormalizedMessage,
)


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
        channel_name="general",
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
    assert 'id="guild-id"' not in response.text
    assert 'id="channel-id"' not in response.text
    assert 'id="guild-selector"' in response.text
    assert 'id="max-messages"' in response.text
    assert "READ-ONLY" in response.text
    assert "bot-secret" not in response.text
    assert "client-secret" not in response.text


def test_preview_reuses_loader_and_cleaner_for_safe_message_preview(client, monkeypatch):
    calls = []

    def load(session, guild_id, max_messages):
        calls.append((session, guild_id, max_messages))
        return GuildMessageLoadResult(
            messages=[_message()],
            supported_channels_discovered=2,
            channels_successfully_read=1,
            channels_skipped=1,
        )

    monkeypatch.setattr(discord_ui, "load_guild_messages", load)
    response = client.post(
        "/integrations/discord/ui/load",
        json={"guild_id": "guild-1", "max_messages": 10},
    )

    assert response.status_code == 200
    assert calls[0][1:] == ("guild-1", 10)
    preview = response.json()["messages"][0]
    assert preview["original_content"] == "btw 😀"
    assert preview["cleaned_content"] == "by the way grinning face"
    assert preview["channel_name"] == "general"
    assert preview["attachment_count"] == 0
    assert response.json()["channels_skipped"] == 1
    assert "bot-secret" not in response.text


@pytest.mark.parametrize("max_messages", [0, 101])
def test_preview_rejects_out_of_range_max_messages(client, max_messages):
    response = client.post(
        "/integrations/discord/ui/load",
        json={"guild_id": "guild-1", "max_messages": max_messages},
    )

    assert response.status_code == 422


def test_preview_maps_integration_error_to_safe_client_error(client, monkeypatch):
    def fail(*_):
        raise DiscordIntegrationError("raw internal bot-secret error", 503)

    monkeypatch.setattr(discord_ui, "load_guild_messages", fail)
    response = client.post(
        "/integrations/discord/ui/load",
        json={"guild_id": "guild-1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Discord is temporarily rate limited or unavailable."
    assert "bot-secret" not in response.text


def test_connected_guilds_returns_human_readable_names_without_secrets(client, monkeypatch):
    monkeypatch.setattr(
        discord_ui,
        "list_connected_guilds",
        lambda _: [
            ConnectedGuild(guild_id="guild-1", guild_name="Engineering"),
            ConnectedGuild(guild_id="guild-2", guild_name="Support"),
        ],
    )

    response = client.get("/integrations/discord/ui/guilds")

    assert response.status_code == 200
    assert response.json() == {
        "guilds": [
            {"guild_id": "guild-1", "guild_name": "Engineering"},
            {"guild_id": "guild-2", "guild_name": "Support"},
        ]
    }
    assert "bot-secret" not in response.text


def test_console_script_auto_selects_one_guild_and_shows_named_multi_guild_selector():
    script = (discord_ui.STATIC_DIR / "discord_console.js").read_text(encoding="utf-8")

    assert 'fetch("/integrations/discord/ui/guilds")' in script
    assert "data.guilds.length === 1" in script
    assert "guildSelectorContainer.hidden = true" in script
    assert "guildSelectorContainer.hidden = false" in script
    assert "option.textContent = guild.guild_name" in script
    assert "channel_id: formData.get" not in script


def test_gui_introduces_only_read_endpoints_and_preserves_auth_routes():
    paths = main_module.app.openapi()["paths"]

    assert set(paths["/integrations/discord/ui"]) == {"get"}
    assert set(paths["/integrations/discord/ui/guilds"]) == {"get"}
    assert set(paths["/integrations/discord/ui/load"]) == {"post"}
    assert "/integrations/discord/authorize" in paths
    assert "/integrations/discord/callback" in paths
