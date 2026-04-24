from __future__ import annotations

import asyncio

import pytest

from harness_telegram.approvals import (
    PendingApprovalStore,
    build_approval_keyboard,
    decode_approval_callback,
    encode_approval_callback,
    generate_approval_id,
)
from harness_telegram.types import ApprovalButton, ApprovalRequest


def test_callback_roundtrip_fits_telegram_limit():
    approval_id = generate_approval_id()
    for action in ("view", "approve", "reject", "needs_revision", "always_allow"):
        encoded = encode_approval_callback(approval_id, action)
        assert len(encoded.encode("utf-8")) <= 64
        assert decode_approval_callback(encoded) == (approval_id, action)


def test_keyboard_uses_request_buttons():
    request = ApprovalRequest(
        approval_id="abc12345",
        buttons=[
            ApprovalButton(label="View", action="view"),
            ApprovalButton(label="Approve", action="approve"),
            ApprovalButton(label="Reject", action="reject"),
        ],
    )
    keyboard = build_approval_keyboard(request)
    row = keyboard["inline_keyboard"][0]
    assert [button["text"] for button in row] == ["View", "Approve", "Reject"]
    assert row[0]["callback_data"] == "ha:abc12345:v"


def test_store_resolve_signals_event():
    store = PendingApprovalStore()
    pending = store.register(ApprovalRequest(approval_id="abc12345"))

    assert store.resolve("abc12345", "approve")
    assert pending.result == "approve"
    assert pending.event.is_set()


def test_store_cleanup_expired_sets_default_result():
    store = PendingApprovalStore()
    pending = store.register(ApprovalRequest(approval_id="abc12345", timeout_seconds=0))

    assert store.cleanup_expired() == 1
    assert pending.result == "reject"
    assert pending.event.is_set()


@pytest.mark.asyncio
async def test_async_approval_wait_flow():
    store = PendingApprovalStore()
    pending = store.register(ApprovalRequest(approval_id="abc12345"))

    async def approve_later():
        await asyncio.sleep(0.01)
        store.resolve("abc12345", "approve")

    asyncio.create_task(approve_later())
    await asyncio.wait_for(pending.event.wait(), timeout=1)
    assert pending.result == "approve"

