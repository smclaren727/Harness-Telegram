from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_telegram.backend import (
    EmacsHarnessBackend,
    ProcessBackend,
    extract_workflow_approval_requests,
)
from harness_telegram.config import (
    EmacsConfig,
    ProcessAgentConfig,
    ProcessConfig,
    ProcessRouteConfig,
)
from harness_telegram.types import ApprovalDecision, HarnessResult, InboundMessage


def write_harness_config(root: Path) -> None:
    config_dir = root / "Config"
    config_dir.mkdir(parents=True)
    (config_dir / "listeners.json").write_text(
        json.dumps(
            {
                "default_telegram_listener": "telegram-bot",
                "listeners": [
                    {
                        "name": "telegram-bot",
                        "type": "telegram",
                        "route": "live-primary",
                        "context_files": ["Fixtures/context-note.org"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_build_event_preserves_harness_shape_and_new_topic_fields(tmp_path):
    write_harness_config(tmp_path)
    backend = EmacsHarnessBackend(EmacsConfig(harness_root=str(tmp_path)))
    inbound = InboundMessage(
        sender_id="123",
        sender_name="Sean",
        chat_type="group",
        chat_id="-1001",
        chat_title="Ops",
        thread_id="77",
        message_id="42",
        text="hello",
        session_key="agent:default:telegram:group:-1001:thread:77",
    )

    event = backend.build_event(inbound)
    payload = event.to_json_payload()

    assert payload["trigger"] == "telegram"
    assert payload["listener"] == "telegram-bot"
    assert payload["route"] == "live-primary"
    assert payload["source_thread_id"] == "77"
    assert payload["session_key"] == "agent:default:telegram:group:-1001:thread:77"
    assert payload["context_files"] == ["Fixtures/context-note.org"]


@pytest.mark.asyncio
async def test_backend_runs_event_file_with_emacsclient(monkeypatch, tmp_path):
    write_harness_config(tmp_path)
    backend = EmacsHarnessBackend(
        EmacsConfig(
            harness_root=str(tmp_path),
            emacsclient="fake-emacsclient",
            socket_name="socket",
            batch_fallback=False,
        )
    )
    payload = {
        "status": "success",
        "reply": "ok",
        "workflow_run_id": "run-1",
    }

    def fake_run(command, check=False, capture_output=False, text=False, timeout=None):
        assert command[0] == "fake-emacsclient"
        assert "--eval" in command
        return SimpleNamespace(stdout=json.dumps(json.dumps(payload)))

    monkeypatch.setattr("harness_telegram.backend.subprocess.run", fake_run)
    result = await backend.handle_message(
        InboundMessage(
            sender_id="123",
            sender_name="Sean",
            chat_type="direct",
            chat_id="123",
            message_id="42",
            text="hello",
            session_key="agent:default:main",
        )
    )

    assert result.reply == "ok"
    event_files = list((tmp_path / "Runtime" / "State" / "Harness-Telegram").glob("*.json"))
    assert len(event_files) == 1
    assert json.loads(event_files[0].read_text())["text"] == "hello"


def test_extract_workflow_approval_requests_from_compat_reply():
    result = HarnessResult.from_payload(
        {
            "status": "success",
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
        }
    )

    requests = extract_workflow_approval_requests(result)

    assert len(requests) == 1
    request = requests[0]
    assert request.kind == "workflow"
    assert request.event_text_for("approve") == "workflow-approve: run-1"
    assert [button.action for button in request.buttons] == ["view", "approve", "reject"]


@pytest.mark.asyncio
async def test_handle_workflow_approval_emits_compat_event(monkeypatch, tmp_path):
    write_harness_config(tmp_path)
    backend = EmacsHarnessBackend(
        EmacsConfig(harness_root=str(tmp_path), emacsclient="fake", batch_fallback=False)
    )
    seen_payloads: list[dict] = []

    def fake_run(command, check=False, capture_output=False, text=False, timeout=None):
        event_dir = tmp_path / "Runtime" / "State" / "Harness-Telegram"
        event_path = sorted(event_dir.glob("*.json"))[-1]
        seen_payloads.append(json.loads(event_path.read_text()))
        return SimpleNamespace(stdout=json.dumps(json.dumps({"status": "success", "reply": "done"})))

    monkeypatch.setattr("harness_telegram.backend.subprocess.run", fake_run)
    result = await backend.handle_approval(
        ApprovalDecision(
            approval_id="workflow-run-1",
            action="approve",
            chat_id="123",
            request=extract_workflow_approval_requests(
                HarnessResult.from_payload(
                    {
                        "reply": "workflow-view: run-1\nworkflow-approve: run-1\nworkflow-deny: run-1"
                    }
                )
            )[0],
        )
    )

    assert result is not None
    assert result.reply == "done"
    assert seen_payloads[-1]["text"] == "workflow-approve: run-1"


@pytest.mark.asyncio
async def test_process_backend_runs_configured_agent_with_session_env(tmp_path):
    backend = ProcessBackend(
        ProcessConfig(
            default_agent="codex",
            state_root=str(tmp_path),
            agents={
                "codex": ProcessAgentConfig(
                    command=[
                        sys.executable,
                        "-c",
                        (
                            "import os, sys; "
                            "print(os.environ['HARNESS_TELEGRAM_AGENT_ID']); "
                            "print(os.environ['HARNESS_TELEGRAM_SESSION_KEY']); "
                            "print(sys.stdin.read().strip())"
                        ),
                    ],
                )
            },
        )
    )

    result = await backend.handle_message(
        InboundMessage(
            sender_id="123",
            sender_name="Sean",
            chat_type="direct",
            chat_id="123",
            message_id="42",
            text="build this",
            session_key="agent:codex:main",
            agent_id="codex",
        )
    )

    assert result.status == "success"
    assert "codex" in (result.reply or "")
    assert "agent:codex:main" in (result.reply or "")
    assert "build this" in (result.reply or "")
    events = list((tmp_path / "sessions" / "agent-codex-main").glob("events.jsonl"))
    assert len(events) == 1


@pytest.mark.asyncio
async def test_process_backend_routes_chat_to_agent(tmp_path):
    backend = ProcessBackend(
        ProcessConfig(
            default_agent="codex",
            state_root=str(tmp_path),
            routes=[ProcessRouteConfig(chat_id="-1001", thread_id="77", agent="claude")],
            agents={
                "codex": ProcessAgentConfig(
                    command=[sys.executable, "-c", "print('codex')"],
                    input_mode="none",
                ),
                "claude": ProcessAgentConfig(
                    command=[sys.executable, "-c", "print('claude')"],
                    input_mode="none",
                ),
            },
        )
    )

    result = await backend.handle_message(
        InboundMessage(
            sender_id="123",
            sender_name="Sean",
            chat_type="group",
            chat_id="-1001",
            thread_id="77",
            message_id="42",
            text="hello",
            session_key="agent:codex:telegram:group:-1001:thread:77",
            agent_id="codex",
        )
    )

    assert result.reply == "claude"
