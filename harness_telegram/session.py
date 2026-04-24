"""Canonical session key helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionKey:
    agent_id: str
    channel: str
    chat_type: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None

    def __str__(self) -> str:
        if self.channel == "main":
            return f"agent:{self.agent_id}:main"
        base = f"agent:{self.agent_id}:{self.channel}:{self.chat_type}:{self.peer_id}"
        if self.thread_id:
            base += f":thread:{self.thread_id}"
        return base


def parse_session_key(key: str) -> SessionKey:
    key = key.strip().lower()
    if not key:
        raise ValueError("session key must not be empty")
    parts = key.split(":")
    if len(parts) < 3 or parts[0] != "agent":
        raise ValueError(f"session key must start with 'agent:': {key!r}")
    agent_id = parts[1]
    if not agent_id:
        raise ValueError(f"agent_id must not be empty: {key!r}")
    if parts[2] == "main" and len(parts) == 3:
        return SessionKey(agent_id=agent_id, channel="main")
    if len(parts) < 5:
        raise ValueError(f"session key must have at least 5 parts: {key!r}")
    thread_id = None
    if len(parts) > 5:
        if len(parts) != 7 or parts[5] != "thread":
            raise ValueError(f"invalid thread suffix in session key: {key!r}")
        thread_id = parts[6]
    return SessionKey(
        agent_id=agent_id,
        channel=parts[2],
        chat_type=parts[3],
        peer_id=parts[4],
        thread_id=thread_id,
    )


def build_session_key(
    *,
    agent_id: str,
    channel: str,
    chat_type: str,
    peer_id: str,
    thread_id: str | None = None,
    per_peer_mode: bool = False,
) -> str:
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    agent_id = agent_id.lower().strip()
    channel = channel.lower().strip()
    chat_type = chat_type.lower().strip()
    peer_id = str(peer_id).lower().strip()
    normalized_thread = str(thread_id or "").lower().strip()

    if chat_type == "direct" and not per_peer_mode and not normalized_thread:
        return f"agent:{agent_id}:main"

    base = f"agent:{agent_id}:{channel}:{chat_type}:{peer_id}"
    if normalized_thread:
        base += f":thread:{normalized_thread}"
    return base

