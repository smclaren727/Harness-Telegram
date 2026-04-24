"""Public data contracts for Harness Telegram."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ApprovalAction = Literal["view", "approve", "reject", "needs_revision", "always_allow"]


class Attachment(BaseModel):
    """A Telegram attachment normalized for the harness."""

    type: Literal["image", "file"]
    uri: str
    name: str | None = None
    mime: str | None = None
    size_bytes: int | None = None


class ApprovalButton(BaseModel):
    """One inline approval button."""

    label: str
    action: ApprovalAction


def default_approval_buttons() -> list[ApprovalButton]:
    return [
        ApprovalButton(label="Approve", action="approve"),
        ApprovalButton(label="Reject", action="reject"),
        ApprovalButton(label="Needs revision", action="needs_revision"),
        ApprovalButton(label="Always allow", action="always_allow"),
    ]


class ApprovalRequest(BaseModel):
    """A request that can be rendered as Telegram inline buttons."""

    approval_id: str
    title: str = "Approval request"
    body: str = ""
    kind: str = "generic"
    run_id: str | None = None
    timeout_seconds: float = 300.0
    event_text_by_action: dict[str, str] = Field(default_factory=dict)
    buttons: list[ApprovalButton] = Field(default_factory=default_approval_buttons)
    metadata: dict[str, str] = Field(default_factory=dict)

    def summary_text(self) -> str:
        lines = [f"*{self.title}*"]
        if self.run_id:
            lines.append(f"Run: `{self.run_id}`")
        if self.body:
            lines.append(self.body)
        return "\n".join(lines)

    def event_text_for(self, action: str) -> str | None:
        mapped = self.event_text_by_action.get(action)
        if mapped:
            return mapped
        if self.kind == "workflow" and self.run_id:
            if action == "view":
                return f"workflow-view: {self.run_id}"
            if action == "approve":
                return f"workflow-approve: {self.run_id}"
            if action == "reject":
                return f"workflow-deny: {self.run_id}"
        return None


class ApprovalDecision(BaseModel):
    """An operator decision produced by a Telegram callback query."""

    approval_id: str
    action: ApprovalAction
    chat_id: str
    sender_id: str = ""
    message_id: str | None = None
    request: ApprovalRequest | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class InboundMessage(BaseModel):
    """Normalized message from Telegram."""

    channel: Literal["telegram"] = "telegram"
    sender_id: str
    sender_name: str = ""
    chat_type: Literal["direct", "group", "channel"]
    chat_id: str
    chat_title: str = ""
    thread_id: str | None = None
    message_id: str
    text: str
    attachments: list[Attachment] = Field(default_factory=list)
    reply_to_message_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    session_key: str = ""
    agent_id: str = "default"


class OutboundMessage(BaseModel):
    channel: Literal["telegram"] = "telegram"
    chat_id: str
    text: str
    reply_to_message_id: str | None = None
    thread_id: str | None = None


class HarnessEvent(BaseModel):
    """Event payload sent to Emacs-Harness."""

    event_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    trigger: Literal["telegram"] = "telegram"
    text: str
    listener: str | None = None
    route: str | None = None
    provider: str | None = None
    source_chat_id: str
    source_chat_title: str = ""
    source_message_id: str
    source_thread_id: str | None = None
    sender_title: str = ""
    session_key: str | None = None
    context_files: list[str] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    approval_requests: list[ApprovalRequest] = Field(default_factory=list)

    def to_json_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class HarnessResult(BaseModel):
    """Structured result returned by a harness backend."""

    status: str = "success"
    reply: str | None = None
    error: str | None = None
    approval_requests: list[ApprovalRequest] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HarnessResult":
        approval_payloads = payload.get("approval_requests") or []
        approvals = [
            item if isinstance(item, ApprovalRequest) else ApprovalRequest.model_validate(item)
            for item in approval_payloads
        ]
        return cls(
            status=str(payload.get("status") or "success"),
            reply=payload.get("reply"),
            error=payload.get("error"),
            approval_requests=approvals,
            raw=dict(payload),
        )

    def operator_text(self) -> str | None:
        if self.reply and self.reply.strip():
            return self.reply
        if self.error and self.error.strip():
            return self.error
        return None

