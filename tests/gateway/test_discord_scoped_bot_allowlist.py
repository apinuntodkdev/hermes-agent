"""Discord bot allowlist is profile-scoped, channel-exact, and human-neutral."""

from types import SimpleNamespace

import discord
import pytest

from gateway.config import PlatformConfig
from gateway.session import Platform, SessionSource
from plugins.platforms.discord.adapter import DiscordAdapter


def _adapter_and_message(*, bot_id: int, channel_id: int):
    adapter = DiscordAdapter(PlatformConfig(
        enabled=True,
        token="test-token",
        extra={
            "bot_allowlist": {"123": ["456"]},
            "bots_require_inline_mention": True,
        },
    ))
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=999))
    message = SimpleNamespace(
        id=10,
        author=SimpleNamespace(id=bot_id, bot=True),
        channel=SimpleNamespace(id=channel_id, name="meeting", guild=SimpleNamespace()),
        type=discord.MessageType.default,
        mentions=[],
        content="<@999> hello",
    )
    return adapter, message


def test_rejects_unlisted_bot_even_with_inline_mention(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, message = _adapter_and_message(bot_id=789, channel_id=123)
    assert adapter._discord_message_admission(message, claim=False)[0] is False


def test_rejects_listed_bot_in_another_channel(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, message = _adapter_and_message(bot_id=456, channel_id=4567)
    assert adapter._discord_message_admission(message, claim=False)[0] is False


def test_accepts_listed_bot_only_when_explicitly_mentioned(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, message = _adapter_and_message(bot_id=456, channel_id=123)
    assert adapter._discord_message_admission(message, claim=False)[0] is True
    message.content = "hello without mention"
    assert adapter._discord_message_admission(message, claim=False)[0] is False
    # A Discord reply-ping in mentions is not a typed inline @mention.
    message.mentions = [adapter._client.user]
    assert adapter._discord_message_admission(message, claim=False)[0] is False


def test_accepts_listed_bot_in_thread_under_allowed_parent(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, message = _adapter_and_message(bot_id=456, channel_id=777)
    message.channel.parent_id = 123
    assert adapter._discord_message_admission(message, claim=False)[0] is True
    message.channel.parent_id = 999
    assert adapter._discord_message_admission(message, claim=False)[0] is False


def test_config_cli_json_string_is_enforced(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, message = _adapter_and_message(bot_id=456, channel_id=123)
    adapter.config.extra["bot_allowlist"] = '{"123":["456"]}'
    assert adapter._discord_message_admission(message, claim=False)[0] is True
    message.author.id = 789
    assert adapter._discord_message_admission(message, claim=False)[0] is False
    adapter.config.extra["bot_allowlist"] = '{invalid json'
    message.author.id = 456
    assert adapter._discord_message_admission(message, claim=False)[0] is False


def test_invalid_bot_allowlist_fails_closed(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, message = _adapter_and_message(bot_id=456, channel_id=123)
    for malformed in (None, {}, "*", {"123": "456"}, {"123": [True]}):
        adapter.config.extra["bot_allowlist"] = malformed
        assert adapter._discord_message_admission(message, claim=False)[0] is False


@pytest.mark.parametrize("allow_bots", ["mentions", "all"])
def test_gateway_denies_bot_when_profile_adapter_is_missing(monkeypatch, allow_bots):
    from gateway.run import GatewayRunner

    monkeypatch.setenv("DISCORD_ALLOW_BOTS", allow_bots)
    default_adapter, _ = _adapter_and_message(bot_id=456, channel_id=123)
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.DISCORD: default_adapter}
    runner._profile_adapters = {"writer": {}}
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        chat_type="channel",
        user_id="456",
        is_bot=True,
        profile="writer",
    )

    assert runner._adapter_for_source(source) is None
    assert runner._is_user_authorized(source) is False


@pytest.mark.parametrize("allow_bots", ["mentions", "all"])
@pytest.mark.parametrize("policy", [{"123": ["456"]}, None])
def test_gateway_denies_bot_when_scoped_policy_has_no_checker(monkeypatch, allow_bots, policy):
    from gateway.run import GatewayRunner

    monkeypatch.setenv("DISCORD_ALLOW_BOTS", allow_bots)
    monkeypatch.setenv("DISCORD_ALLOW_ALL_USERS", "true")
    runner = object.__new__(GatewayRunner)
    runner.__dict__["adapters"] = {Platform.DISCORD: SimpleNamespace(config=PlatformConfig(
        enabled=True,
        extra={"bot_allowlist": policy},
    ))}
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        chat_type="channel",
        user_id="789",
        is_bot=True,
    )

    assert runner._is_user_authorized(source) is False


def test_gateway_denies_bot_when_default_adapter_is_missing(monkeypatch):
    from gateway.run import GatewayRunner

    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "all")
    monkeypatch.setenv("DISCORD_ALLOW_ALL_USERS", "true")
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        chat_type="channel",
        user_id="789",
        is_bot=True,
    )

    assert runner._is_user_authorized(source) is False


def test_gateway_denies_bot_when_profile_checker_is_not_callable(monkeypatch):
    from gateway.run import GatewayRunner

    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "all")
    runner = object.__new__(GatewayRunner)
    runner.__dict__["_profile_adapters"] = {"writer": {Platform.DISCORD: SimpleNamespace(
        config=PlatformConfig(extra={"bot_allowlist": {"123": ["456"]}}),
        _scoped_bot_allowed=None,
    )}}
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        chat_type="channel",
        user_id="789",
        is_bot=True,
        profile="writer",
    )

    assert runner._is_user_authorized(source) is False


@pytest.mark.parametrize("allow_bots", ["mentions", "all"])
def test_gateway_keeps_unscoped_legacy_bot_bypass(monkeypatch, allow_bots):
    from gateway.run import GatewayRunner

    monkeypatch.setenv("DISCORD_ALLOW_BOTS", allow_bots)
    runner = object.__new__(GatewayRunner)
    runner.__dict__["adapters"] = {Platform.DISCORD: SimpleNamespace(
        config=PlatformConfig(extra={}),
    )}
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        chat_type="channel",
        user_id="789",
        is_bot=True,
    )

    assert runner._is_user_authorized(source) is True


def test_gateway_keeps_explicitly_trusted_relay_bot_without_discord_adapter(monkeypatch):
    from gateway.run import GatewayRunner

    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "none")
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        chat_type="channel",
        user_id="789",
        is_bot=True,
        delivered_via_upstream_relay=True,
    )

    assert runner._is_user_authorized(source) is True


def test_gateway_rejects_unlisted_bot_even_when_allow_bots_mentions(monkeypatch):
    from gateway.run import GatewayRunner
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, _ = _adapter_and_message(bot_id=456, channel_id=123)
    runner = object.__new__(GatewayRunner)
    runner.pairing_store = SimpleNamespace(is_approved=lambda *_a, **_kw: False)
    runner._adapter_for_source = lambda source: adapter
    def source(bot_id, channel_id):
        return SessionSource(
            platform=Platform.DISCORD,
            chat_id=str(channel_id),
            chat_type="channel",
            user_id=str(bot_id),
            is_bot=True,
        )
    assert runner._is_user_authorized(source(789, 123)) is False
    assert runner._is_user_authorized(source(456, 4567)) is False
    assert runner._is_user_authorized(source(456, 123)) is True


def test_gateway_authorizes_listed_bot_after_auto_threading(monkeypatch):
    from gateway.run import GatewayRunner

    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, _ = _adapter_and_message(bot_id=456, channel_id=123)
    runner = object.__new__(GatewayRunner)
    runner.__dict__["pairing_store"] = SimpleNamespace(is_approved=lambda *_a, **_kw: False)
    runner._adapter_for_source = lambda source: adapter
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="777",  # Auto-created thread for the incoming message.
        parent_chat_id="123",  # The verified parent channel is allowlisted.
        chat_type="thread",
        user_id="456",
        is_bot=True,
    )

    assert runner._is_user_authorized(source) is True


def test_gateway_rejects_bot_from_other_parent_after_auto_threading(monkeypatch):
    from gateway.run import GatewayRunner

    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, _ = _adapter_and_message(bot_id=456, channel_id=123)
    runner = object.__new__(GatewayRunner)
    runner.__dict__["pairing_store"] = SimpleNamespace(is_approved=lambda *_a, **_kw: False)
    runner._adapter_for_source = lambda source: adapter
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="777",
        parent_chat_id="999",
        chat_type="thread",
        user_id="456",
        is_bot=True,
    )

    assert runner._is_user_authorized(source) is False
    source.parent_chat_id = "123"
    source.user_id = "789"
    assert runner._is_user_authorized(source) is False


@pytest.mark.asyncio
async def test_history_drops_unlisted_bot_messages(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, _ = _adapter_and_message(bot_id=456, channel_id=123)
    def bot_msg(bot_id, content, msg_id):
        return SimpleNamespace(
            id=msg_id,
            type=discord.MessageType.default,
            author=SimpleNamespace(id=bot_id, bot=True, display_name=f"Bot{bot_id}"),
            content=content,
            attachments=[],
        )
    class Channel:
        id = 123
        async def history(self, **kwargs):
            yield bot_msg(789, "untrusted bot instructions", 20)
            yield bot_msg(456, "trusted bot update", 19)
    context = await adapter._fetch_channel_context(Channel(), before=SimpleNamespace(id=30))
    assert "trusted bot update" in context
    assert "untrusted bot instructions" not in context


@pytest.mark.asyncio
async def test_thread_history_includes_only_bots_from_allowed_parent(monkeypatch):
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "mentions")
    adapter, _ = _adapter_and_message(bot_id=456, channel_id=777)

    def bot_msg(bot_id, content, msg_id):
        return SimpleNamespace(
            id=msg_id,
            type=discord.MessageType.default,
            author=SimpleNamespace(id=bot_id, bot=True, display_name=f"Bot{bot_id}"),
            content=content,
            attachments=[],
        )

    class Channel:
        id = 777
        parent_id = 123

        async def history(self, **kwargs):
            yield bot_msg(789, "untrusted bot instructions", 20)
            yield bot_msg(456, "trusted bot update", 19)

    context = await adapter._fetch_channel_context(Channel(), before=SimpleNamespace(id=30))
    assert "trusted bot update" in context
    assert "untrusted bot instructions" not in context
