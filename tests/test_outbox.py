from __future__ import annotations

import json

import pytest

from harness_telegram.outbox import HarnessTelegramOutbox
from harness_telegram.types import ApprovalRequest, HarnessResult


class RecordingAdapter:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[str, HarnessResult, str | None]] = []

    async def send_harness_result(
        self,
        chat_id,
        result,
        *,
        agent_id=None,
        session_key=None,
    ):
        self.sent.append((str(chat_id), result, session_key))
        return self.ok


def write_pending(tmp_path, name: str, payload: dict) -> None:
    pending = tmp_path / "Runtime" / "State" / "Harness-Telegram" / "Outbox" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / name).write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_outbox_sends_to_operator_and_extracts_workflow_approval(tmp_path):
    write_pending(
        tmp_path,
        "shadow.json",
        {
            "notification_id": "shadow",
            "workflow_run_id": "run-1",
            "reply": "\n".join(
                [
                    "Shadowed workflow inbox-triage",
                    "Review commands:",
                    "  workflow-view: run-1",
                    "  workflow-approve: run-1",
                    "  workflow-deny: run-1",
                ]
            ),
            "session_key": "workflow:auto-shadow:inbox-triage",
        },
    )
    adapter = RecordingAdapter()
    outbox = HarnessTelegramOutbox(
        harness_root=tmp_path,
        adapter=adapter,  # type: ignore[arg-type]
        operator_chat_id="operator-chat",
    )

    destinations = await outbox.process_once()

    assert len(destinations) == 1
    assert destinations[0].parent.name == "sent"
    assert adapter.sent[0][0] == "operator-chat"
    assert adapter.sent[0][2] == "workflow:auto-shadow:inbox-triage"
    assert adapter.sent[0][1].approval_requests[0].event_text_for("approve") == (
        "workflow-approve: run-1"
    )


@pytest.mark.asyncio
async def test_outbox_preserves_payload_approval_requests(tmp_path):
    write_pending(
        tmp_path,
        "approval.json",
        {
            "notification_id": "approval",
            "chat_id": "direct-chat",
            "reply": "please review",
            "approval_requests": [
                ApprovalRequest(
                    approval_id="approval-1",
                    kind="workflow",
                    run_id="run-2",
                ).model_dump()
            ],
        },
    )
    adapter = RecordingAdapter()
    outbox = HarnessTelegramOutbox(
        harness_root=tmp_path,
        adapter=adapter,  # type: ignore[arg-type]
        operator_chat_id="operator-chat",
    )

    await outbox.process_once()

    assert adapter.sent[0][0] == "direct-chat"
    assert adapter.sent[0][1].approval_requests[0].approval_id == "approval-1"


@pytest.mark.asyncio
async def test_outbox_retries_then_moves_to_failed(tmp_path):
    write_pending(tmp_path, "failing.json", {"notification_id": "failing", "reply": "nope"})
    adapter = RecordingAdapter(ok=False)
    outbox = HarnessTelegramOutbox(
        harness_root=tmp_path,
        adapter=adapter,  # type: ignore[arg-type]
        operator_chat_id="operator-chat",
        max_attempts=2,
    )

    assert await outbox.process_once() == []
    pending = (
        tmp_path
        / "Runtime"
        / "State"
        / "Harness-Telegram"
        / "Outbox"
        / "pending"
        / "failing.json"
    )
    assert json.loads(pending.read_text())["_outbox_attempts"] == 1

    destinations = await outbox.process_once()

    assert len(destinations) == 1
    assert destinations[0].parent.name == "failed"
    assert not pending.exists()
