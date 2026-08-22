from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.integrations.discord.discord_auth_service import DiscordIntegrationError
from app.integrations.discord.discord_loader import (
    discover_guild_channels,
    list_connected_guilds,
    load_guild_messages,
)
from app.integrations.discord.models import DiscordConnection


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


def _add_connection(session, guild_id="guild-1", user_id="user-1"):
    session.add(
        DiscordConnection(
            discord_user_id=user_id,
            guild_id=guild_id,
            access_token="oauth-access",
            refresh_token="oauth-refresh",
            token_type="Bearer",
            scopes="identify bot",
            expires_at=datetime.now(timezone.utc),
        )
    )
    session.commit()


def _message(message_id, timestamp="2026-01-01T00:00:00+00:00"):
    return {
        "id": message_id,
        "content": "message content",
        "author": {"id": "author-1", "username": "author"},
        "timestamp": timestamp,
        "attachments": [],
    }


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_connected_guilds_are_deduplicated_and_resolved_with_bot_auth(session):
    _add_connection(session, user_id="user-1")
    _add_connection(session, user_id="user-2")
    headers = []

    def handler(request):
        headers.append(request.headers["Authorization"])
        assert request.url.path.endswith("/guilds/guild-1")
        return httpx.Response(200, json={"id": "guild-1", "name": "Engineering"})

    with _client(handler) as client:
        guilds = list_connected_guilds(session, http_client=client)

    assert guilds[0].guild_id == "guild-1"
    assert guilds[0].guild_name == "Engineering"
    assert headers == ["Bot bot-secret"]


def test_discovery_rejects_unconnected_guild_and_filters_supported_channel_types(session):
    with pytest.raises(DiscordIntegrationError) as error:
        discover_guild_channels(session, "guild-1", http_client=_client(lambda _: None))
    assert error.value.status_code == 404

    _add_connection(session)

    def handler(request):
        assert request.headers["Authorization"] == "Bot bot-secret"
        return httpx.Response(
            200,
            json=[
                {"id": "text", "name": "general", "type": 0},
                {"id": "announcement", "name": "news", "type": 5},
                {"id": "voice", "name": "voice", "type": 2},
            ],
        )

    with _client(handler) as client:
        channels = discover_guild_channels(session, "guild-1", http_client=client)

    assert [(channel.channel_id, channel.channel_name) for channel in channels] == [
        ("text", "general"),
        ("announcement", "news"),
    ]


def test_loads_all_readable_channels_with_metadata_order_and_per_channel_limit(session):
    _add_connection(session)
    message_requests = []

    def handler(request):
        path = request.url.path
        assert request.headers["Authorization"] == "Bot bot-secret"
        if path.endswith("/guilds/guild-1/channels"):
            return httpx.Response(
                200,
                json=[
                    {"id": "channel-a", "name": "alpha", "type": 0},
                    {"id": "channel-b", "name": "beta", "type": 5},
                ],
            )
        if path.endswith("/channels/channel-a") or path.endswith("/channels/channel-b"):
            return httpx.Response(200, json={"guild_id": "guild-1"})
        if path.endswith("/channels/channel-a/messages"):
            message_requests.append(request)
            return httpx.Response(
                200,
                json=[
                    _message("a-new", "2026-01-02T00:00:00+00:00"),
                    _message("a-old", "2026-01-01T00:00:00+00:00"),
                    _message("a-extra", "2025-12-31T00:00:00+00:00"),
                ],
            )
        if path.endswith("/channels/channel-b/messages"):
            message_requests.append(request)
            return httpx.Response(200, json=[_message("b-new", "2026-01-03T00:00:00+00:00")])
        raise AssertionError(path)

    with _client(handler) as client:
        result = load_guild_messages(
            session,
            "guild-1",
            max_messages_per_channel=2,
            http_client=client,
        )

    assert [message.message_id for message in result.messages] == ["b-new", "a-new", "a-old"]
    assert [message.channel_name for message in result.messages] == ["beta", "alpha", "alpha"]
    assert result.supported_channels_discovered == 2
    assert result.channels_successfully_read == 2
    assert result.channels_skipped == 0
    assert all(request.url.params["limit"] == "2" for request in message_requests)


@pytest.mark.parametrize("status_code", [403, 404])
def test_skips_unreadable_channels_and_continues(session, status_code):
    _add_connection(session)

    def handler(request):
        path = request.url.path
        if path.endswith("/guilds/guild-1/channels"):
            return httpx.Response(
                200,
                json=[
                    {"id": "unreadable", "name": "private", "type": 0},
                    {"id": "readable", "name": "general", "type": 0},
                ],
            )
        if path.endswith("/channels/unreadable"):
            return httpx.Response(status_code)
        if path.endswith("/channels/readable"):
            return httpx.Response(200, json={"guild_id": "guild-1"})
        if path.endswith("/channels/readable/messages"):
            return httpx.Response(200, json=[_message("message-1")])
        raise AssertionError(path)

    with _client(handler) as client:
        result = load_guild_messages(session, "guild-1", http_client=client)

    assert [message.channel_name for message in result.messages] == ["general"]
    assert result.channels_successfully_read == 1
    assert result.channels_skipped == 1


@pytest.mark.parametrize(
    ("status_code", "payload", "expected_status"),
    [(429, {"retry_after": 11}, 503), (200, {"id": "not-a-list"}, 502)],
)
def test_discovery_rejects_bounded_rate_limits_and_malformed_responses(
    session, status_code, payload, expected_status
):
    _add_connection(session)

    def handler(_):
        return httpx.Response(status_code, json=payload)

    with _client(handler) as client:
        with pytest.raises(DiscordIntegrationError) as error:
            discover_guild_channels(
                session,
                "guild-1",
                http_client=client,
                sleep_function=lambda _: pytest.fail("bounded retry must not sleep"),
            )

    assert error.value.status_code == expected_status


def test_guild_discovery_permission_failure_is_safely_mapped(session):
    _add_connection(session)

    with _client(lambda _: httpx.Response(403)) as client:
        with pytest.raises(DiscordIntegrationError) as error:
            discover_guild_channels(session, "guild-1", http_client=client)

    assert error.value.status_code == 403
