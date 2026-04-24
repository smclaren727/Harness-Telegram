"""Generic inline approval primitives for Telegram callback buttons."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Mapping
from uuid import uuid4

from harness_telegram.types import ApprovalAction, ApprovalRequest

CALLBACK_PREFIX = "ha"
ACTION_TO_CHAR: Mapping[str, str] = {
    "view": "v",
    "approve": "y",
    "reject": "n",
    "needs_revision": "r",
    "always_allow": "a",
}
CHAR_TO_ACTION = {value: key for key, value in ACTION_TO_CHAR.items()}
DEFAULT_EXPIRE_ACTION: ApprovalAction = "reject"


def generate_approval_id() -> str:
    return uuid4().hex[:8]


def is_pending_approval_expired(created_at: str, timeout_seconds: float) -> bool:
    created = datetime.fromisoformat(created_at)
    return (datetime.now(UTC) - created).total_seconds() > timeout_seconds


def encode_callback_data(prefix: str, item_id: str, action_char: str) -> str:
    data = f"{prefix}:{item_id}:{action_char}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("Telegram callback_data must fit in 64 bytes")
    return data


def decode_callback_data(data: str, *, prefix: str) -> tuple[str, str] | None:
    parts = str(data or "").split(":")
    if len(parts) != 3 or parts[0] != prefix:
        return None
    return parts[1], parts[2]


def encode_approval_callback(approval_id: str, action: str) -> str:
    action_char = ACTION_TO_CHAR.get(action, action)
    if action_char not in CHAR_TO_ACTION:
        raise ValueError(f"unknown approval action: {action!r}")
    return encode_callback_data(CALLBACK_PREFIX, approval_id, action_char)


def decode_approval_callback(data: str) -> tuple[str, ApprovalAction] | None:
    decoded = decode_callback_data(data, prefix=CALLBACK_PREFIX)
    if decoded is None:
        return None
    approval_id, action_char = decoded
    action = CHAR_TO_ACTION.get(action_char)
    if action is None:
        return None
    return approval_id, action  # type: ignore[return-value]


def build_approval_keyboard(request: ApprovalRequest) -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": button.label,
                    "callback_data": encode_approval_callback(
                        request.approval_id,
                        button.action,
                    ),
                }
                for button in request.buttons
            ]
        ]
    }


@dataclass
class PendingApproval:
    request: ApprovalRequest
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: ApprovalAction | None = None

    def is_expired(self) -> bool:
        return is_pending_approval_expired(self.created_at, self.request.timeout_seconds)


class PendingApprovalStore:
    """In-memory pending approval store for one Telegram daemon process."""

    def __init__(self, *, expire_action: ApprovalAction = DEFAULT_EXPIRE_ACTION) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._expire_action = expire_action

    def register(self, request: ApprovalRequest) -> PendingApproval:
        pending = PendingApproval(request=request)
        self._pending[request.approval_id] = pending
        return pending

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._pending.get(approval_id)

    def resolve(self, approval_id: str, action: ApprovalAction) -> bool:
        pending = self._pending.get(approval_id)
        if pending is None:
            return False
        pending.result = action
        pending.event.set()
        return True

    def clear(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)

    def cleanup_expired(self) -> int:
        expired = [
            (approval_id, pending)
            for approval_id, pending in self._pending.items()
            if pending.is_expired()
        ]
        for approval_id, pending in expired:
            self._pending.pop(approval_id, None)
            if not pending.event.is_set():
                pending.result = self._expire_action
                pending.event.set()
        return len(expired)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

