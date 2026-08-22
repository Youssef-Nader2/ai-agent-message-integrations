from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.integrations.discord.discord_auth_service import DiscordIntegrationError
from app.integrations.discord.discord_loader import load_messages
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


def _add_connection(session, guild_id="guild-1"):
    session.add(
        DiscordConnection(
            discord_user_id="user-1",
            guild_id=guild_id,
            access_token="oauth-access",
            refresh_token="oauth-refresh",
            token_type="Bearer",
            scopes="identify bot",
            expires_at=datetime.now(timezone.utc),
        )
    )
    session.commit()


def _message(message_id: str, **overrides):
    value = {
        "id": message_id,
        "content": "message content",
        "author": {"id": "author-1", "global_name": "Author", "username": "author"},
        "timestamp": "2026-01-01T00:00:00.000000+00:00",
        "attachments": [],
    }
    value.update(overrides)
    return value


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_loader_rejects_missing_connection(session):
    with pytest.raises(DiscordIntegrationError) as error:
        load_messages(session, "guild-1", "channel-1", http_client=_client(lambda _: None))
    assert error.value.status_code == 404


def test_loader_normalizes_one_page_attachments_and_reply(session):
    _add_connection(session)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json=[
                    _message(
                        "message-1",
                        attachments=[{"id": "attachment-1", "filename": "file.txt", "url": "https://example.test/file.txt", "content_type": "text/plain", "size": 12}],
                        message_reference={"message_id": "parent-1"},
                    )
                ],
            )
        return httpx.Response(200, json={"guild_id": "guild-1"})

    with _client(handler) as client:
        messages = load_messages(session, "guild-1", "channel-1", http_client=client)

    assert len(messages) == 1
    assert messages[0].platform == "discord"
    assert messages[0].attachments[0].filename == "file.txt"
    assert messages[0].reply_to_message_id == "parent-1"
    assert "oauth-access" not in repr(messages[0])
    assert "bot-secret" not in repr(messages[0])


def test_loader_normalizes_mentions_and_stickers(session):
    _add_connection(session)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json=[
                    _message(
                        "message-1",
                        mentions=[
                            {"id": "user-1", "global_name": "Display Name", "username": "username"},
                            {"id": "user-2", "global_name": "", "username": "fallback"},
                        ],
                        sticker_items=[
                            {"id": "sticker-1", "name": "Pepe Laugh", "format_type": 1}
                        ],
                    )
                ],
            )
        return httpx.Response(200, json={"guild_id": "guild-1"})

    with _client(handler) as client:
        message = load_messages(session, "guild-1", "channel-1", http_client=client)[0]

    assert [(mention.user_id, mention.display_name) for mention in message.mentions] == [
        ("user-1", "Display Name"),
        ("user-2", "fallback"),
    ]
    assert message.stickers[0].name == "Pepe Laugh"
    assert message.stickers[0].format_type == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"mentions": [{}]},
        {"mentions": "invalid"},
        {"sticker_items": [{"id": "sticker-1", "name": ""}]},
        {"sticker_items": [{"id": "sticker-1", "name": "Sticker", "format_type": True}]},
    ],
)
def test_loader_rejects_malformed_mention_or_sticker_data(session, overrides):
    _add_connection(session)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json=[_message("message-1", **overrides)])
        return httpx.Response(200, json={"guild_id": "guild-1"})

    with _client(handler) as client:
        with pytest.raises(DiscordIntegrationError):
            load_messages(session, "guild-1", "channel-1", http_client=client)


def test_loader_defaults_missing_mentions_and_stickers_to_empty_lists(session):
    _add_connection(session)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json=[_message("message-1")])
        return httpx.Response(200, json={"guild_id": "guild-1"})

    with _client(handler) as client:
        message = load_messages(session, "guild-1", "channel-1", http_client=client)[0]

    assert message.mentions == []
    assert message.stickers == []


def test_loader_rejects_channel_from_another_guild(session):
    _add_connection(session)

    with _client(lambda _: httpx.Response(200, json={"guild_id": "other-guild"})) as client:
        with pytest.raises(DiscordIntegrationError) as error:
            load_messages(session, "guild-1", "channel-1", http_client=client)

    assert error.value.status_code == 403


def test_loader_paginates_and_stops_on_partial_page(session):
    _add_connection(session)

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"guild_id": "guild-1"})
        if request.url.params.get("before") is None:
            return httpx.Response(200, json=[_message(str(number)) for number in range(100, 0, -1)])
        return httpx.Response(200, json=[_message("older-2"), _message("older-1")])

    with _client(handler) as client:
        messages = load_messages(session, "guild-1", "channel-1", 150, http_client=client)

    assert len(messages) == 102
    assert messages[0].message_id == "100"
    assert messages[-1].message_id == "older-1"


def test_loader_handles_empty_channel_and_maximum_limit(session):
    _add_connection(session)

    def empty_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"guild_id": "guild-1"})

    with _client(empty_handler) as client:
        assert load_messages(session, "guild-1", "channel-1", http_client=client) == []

    def limit_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json=[_message("3"), _message("2"), _message("1")])
        return httpx.Response(200, json={"guild_id": "guild-1"})

    with _client(limit_handler) as client:
        messages = load_messages(session, "guild-1", "channel-1", 2, http_client=client)
    assert len(messages) == 2


@pytest.mark.parametrize("status_code", [403, 404])
def test_loader_maps_permission_and_not_found_errors(session, status_code):
    _add_connection(session)
    with _client(lambda _: httpx.Response(status_code)) as client:
        with pytest.raises(DiscordIntegrationError) as error:
            load_messages(session, "guild-1", "channel-1", http_client=client)
    assert error.value.status_code == status_code


def test_loader_retries_one_rate_limit_then_succeeds(session):
    _add_connection(session)
    calls = {"messages": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"guild_id": "guild-1"})
        calls["messages"] += 1
        if calls["messages"] == 1:
            return httpx.Response(429, json={"retry_after": 0})
        return httpx.Response(200, json=[_message("message-1")])

    with _client(handler) as client:
        messages = load_messages(
            session,
            "guild-1",
            "channel-1",
            http_client=client,
            sleep_function=lambda _: None,
        )

    assert len(messages) == 1
    assert calls["messages"] == 2


def test_loader_rejects_repeated_rate_limits(session):
    _add_connection(session)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(429, json={"retry_after": 0})
        return httpx.Response(200, json={"guild_id": "guild-1"})

    with _client(handler) as client:
        with pytest.raises(DiscordIntegrationError) as error:
            load_messages(
                session,
                "guild-1",
                "channel-1",
                http_client=client,
                sleep_function=lambda _: None,
            )

    assert error.value.status_code == 503


def test_loader_rejects_excessive_rate_limit_delay_without_sleeping(session):
    _add_connection(session)
    calls = {"messages": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            calls["messages"] += 1
            return httpx.Response(429, json={"retry_after": 11})
        return httpx.Response(200, json={"guild_id": "guild-1"})

    with _client(handler) as client:
        with pytest.raises(DiscordIntegrationError) as error:
            load_messages(
                session,
                "guild-1",
                "channel-1",
                http_client=client,
                sleep_function=sleeps.append,
            )

    assert error.value.status_code == 503
    assert sleeps == []
    assert calls["messages"] == 1


def test_loader_retries_rate_limit_delay_at_configured_boundary(session):
    _add_connection(session)
    calls = {"messages": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"guild_id": "guild-1"})
        calls["messages"] += 1
        if calls["messages"] == 1:
            return httpx.Response(429, json={"retry_after": 10})
        return httpx.Response(200, json=[_message("message-1")])

    with _client(handler) as client:
        messages = load_messages(
            session,
            "guild-1",
            "channel-1",
            http_client=client,
            sleep_function=sleeps.append,
        )

    assert len(messages) == 1
    assert sleeps == [10.0]
    assert calls["messages"] == 2
