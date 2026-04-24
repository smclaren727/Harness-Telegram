from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness_telegram.approvals import PendingApprovalStore, encode_approval_callback
from harness_telegram.telegram import TelegramAdapter, normalize_telegram_message
from harness_telegram.types import ApprovalRequest, HarnessResult, InboundMessage


def test_direct_message_normalizes_to_inbound_message():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "from": {"id": 12345, "first_name": "Sean"},
            "chat": {"id": 12345, "type": "private"},
            "text": "hello",
        },
    }

    inbound = normalize_telegram_message(update)

    assert inbound is not None
    assert inbound.chat_type == "direct"
    assert inbound.chat_id == "12345"
    assert inbound.text == "hello"
    assert inbound.session_key == "agent:default:main"


def test_group_without_activation_is_ignored():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "from": {"id": 12345, "first_name": "Sean"},
            "chat": {"id": -1001, "type": "supergroup"},
            "text": "background chatter",
        },
    }

    assert normalize_telegram_message(update, bot_username="harness_bot") is None


def test_group_mention_with_forum_topic_keeps_thread_id():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "message_thread_id": 77,
            "from": {"id": 12345, "first_name": "Sean"},
            "chat": {"id": -1001, "type": "supergroup", "title": "Ops"},
            "text": "@harness_bot check this",
            "entities": [{"type": "mention", "offset": 0, "length": 12}],
        },
    }

    inbound = normalize_telegram_message(update, bot_username="harness_bot")

    assert inbound is not None
    assert inbound.thread_id == "77"
    assert inbound.session_key == "agent:default:telegram:group:-1001:thread:77"


def test_slash_command_routes_to_agent():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "from": {"id": 12345, "first_name": "Sean"},
            "chat": {"id": 12345, "type": "private"},
            "text": "/writer draft this",
        },
    }

    inbound = normalize_telegram_message(update)

    assert inbound is not None
    assert inbound.agent_id == "writer"
    assert inbound.text == "draft this"


def test_document_and_photo_attachments_are_normalized():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "from": {"id": 12345, "first_name": "Sean"},
            "chat": {"id": 12345, "type": "private"},
            "caption": "store this",
            "document": {
                "file_id": "doc-file",
                "file_name": "brief.pdf",
                "mime_type": "application/pdf",
                "file_size": 10,
            },
            "photo": [
                {"file_id": "small", "file_size": 1},
                {"file_id": "large", "file_unique_id": "unique", "file_size": 100},
            ],
        },
    }

    inbound = normalize_telegram_message(update)

    assert inbound is not None
    assert [attachment.uri for attachment in inbound.attachments] == [
        "tg://doc-file",
        "tg://large",
    ]
    assert inbound.attachments[0].name == "brief.pdf"


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {"result": {"message_id": 99}}
        self.content = b"file-bytes"
        self.headers = {"content-type": "application/octet-stream"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text)


@pytest.mark.asyncio
async def test_send_message_falls_back_when_markdown_parse_fails(monkeypatch):
    posts: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append(json)
            if len(posts) == 1:
                return FakeResponse(400, "can't parse entities")
            return FakeResponse()

    monkeypatch.setattr("harness_telegram.telegram.httpx.AsyncClient", FakeClient)
    adapter = TelegramAdapter(token="token")

    await adapter.send_message("123", "**hello**")

    assert posts[0]["parse_mode"] == "Markdown"
    assert "parse_mode" not in posts[1]
    assert posts[1]["text"] == "*hello*"


@pytest.mark.asyncio
async def test_handle_update_downloads_attachment_and_sends_result(monkeypatch, tmp_path):
    posts: list[dict] = []

    class FakeSendClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append(json)
            return FakeResponse()

    class UpdateClient:
        async def get(self, url, params=None):
            if url.endswith("/getFile"):
                return FakeResponse(payload={"result": {"file_path": "docs/brief.txt"}})
            return FakeResponse()

        async def post(self, url, json):
            posts.append(json)
            return FakeResponse()

    monkeypatch.setattr("harness_telegram.telegram.httpx.AsyncClient", FakeSendClient)
    adapter = TelegramAdapter(token="token", import_root=tmp_path)
    captured: InboundMessage | None = None

    async def handler(inbound: InboundMessage):
        nonlocal captured
        captured = inbound
        return HarnessResult(reply="done")

    update = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "from": {"id": 12345, "first_name": "Sean"},
            "chat": {"id": 12345, "type": "private"},
            "caption": "save this",
            "document": {"file_id": "doc-file", "file_name": "brief.txt"},
        },
    }

    await adapter._handle_update(update, handler, UpdateClient())

    assert captured is not None
    assert captured.attachments[0].uri.endswith("brief.txt")
    assert (tmp_path / "brief.txt").read_bytes() == b"file-bytes"
    assert posts[-1]["text"] == "done"


@pytest.mark.asyncio
async def test_callback_resolves_approval_and_calls_backend(monkeypatch):
    posts: list[dict] = []

    class FakeSendClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append(json)
            return FakeResponse()

    class CallbackClient:
        async def post(self, url, json):
            posts.append(json)
            return FakeResponse()

    monkeypatch.setattr("harness_telegram.telegram.httpx.AsyncClient", FakeSendClient)
    store = PendingApprovalStore()
    request = ApprovalRequest(
        approval_id="abc12345",
        kind="workflow",
        run_id="run-1",
    )
    store.register(request)
    adapter = TelegramAdapter(token="token", approval_store=store)
    decisions = []

    async def on_approval(decision):
        decisions.append(decision)
        return HarnessResult(reply="approved in harness")

    adapter.on_approval = on_approval
    callback_query = {
        "id": "callback-1",
        "data": encode_approval_callback("abc12345", "approve"),
        "from": {"id": 12345},
        "message": {"message_id": 99, "chat": {"id": 12345}},
    }

    await adapter._handle_callback_query(callback_query, CallbackClient())

    assert store.get("abc12345").result == "approve"
    assert decisions[0].request.run_id == "run-1"
    assert any(post.get("text") == "approved in harness" for post in posts)


def test_allowed_chat_ids_include_operator_chat():
    adapter = TelegramAdapter(
        token="token",
        operator_chat_id="operator-chat",
        allowed_chat_ids=["other-chat"],
    )

    assert adapter._chat_allowed("operator-chat")
    assert adapter._chat_allowed("other-chat")
    assert not adapter._chat_allowed("unknown")


@pytest.mark.asyncio
async def test_send_harness_result_sends_inline_approval_keyboard(monkeypatch):
    posts: list[dict] = []

    class FakeSendClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posts.append(json)
            return FakeResponse()

    monkeypatch.setattr("harness_telegram.telegram.httpx.AsyncClient", FakeSendClient)
    adapter = TelegramAdapter(token="token")

    ok = await adapter.send_harness_result(
        "operator-chat",
        HarnessResult(
            reply="review ready",
            approval_requests=[
                ApprovalRequest(approval_id="approval-1", kind="workflow", run_id="run-1")
            ],
        ),
    )

    assert ok is True
    assert posts[0]["text"] == "review ready"
    assert "reply_markup" in posts[1]
