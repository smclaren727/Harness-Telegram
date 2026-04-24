from __future__ import annotations

import pytest

from harness_telegram.session import build_session_key, parse_session_key


def test_direct_chat_defaults_to_agent_main():
    assert (
        build_session_key(
            agent_id="default",
            channel="telegram",
            chat_type="direct",
            peer_id="123",
        )
        == "agent:default:main"
    )


def test_direct_chat_per_peer_mode_keeps_chat_id():
    assert (
        build_session_key(
            agent_id="default",
            channel="telegram",
            chat_type="direct",
            peer_id="123",
            per_peer_mode=True,
        )
        == "agent:default:telegram:direct:123"
    )


def test_group_forum_topic_appends_thread_id():
    key = build_session_key(
        agent_id="writer",
        channel="telegram",
        chat_type="group",
        peer_id="-1001",
        thread_id="77",
    )
    assert key == "agent:writer:telegram:group:-1001:thread:77"
    parsed = parse_session_key(key)
    assert parsed.agent_id == "writer"
    assert parsed.thread_id == "77"


def test_parse_rejects_invalid_key():
    with pytest.raises(ValueError):
        parse_session_key("telegram:default")

