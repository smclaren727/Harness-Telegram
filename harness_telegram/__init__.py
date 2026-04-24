"""Standalone Telegram transport for harness-style control planes."""

from harness_telegram.approvals import (
    PendingApproval,
    PendingApprovalStore,
    build_approval_keyboard,
    decode_approval_callback,
    encode_approval_callback,
    generate_approval_id,
)
from harness_telegram.backend import Backend, EmacsHarnessBackend
from harness_telegram.config import EmacsConfig, HarnessTelegramConfig, TelegramConfig, load_config
from harness_telegram.session import SessionKey, build_session_key, parse_session_key
from harness_telegram.telegram import TelegramAdapter, normalize_telegram_message
from harness_telegram.types import (
    ApprovalButton,
    ApprovalDecision,
    ApprovalRequest,
    Attachment,
    HarnessEvent,
    HarnessResult,
    InboundMessage,
    OutboundMessage,
)

__all__ = [
    "ApprovalButton",
    "ApprovalDecision",
    "ApprovalRequest",
    "Attachment",
    "Backend",
    "EmacsConfig",
    "EmacsHarnessBackend",
    "HarnessEvent",
    "HarnessResult",
    "HarnessTelegramConfig",
    "InboundMessage",
    "OutboundMessage",
    "PendingApproval",
    "PendingApprovalStore",
    "SessionKey",
    "TelegramAdapter",
    "TelegramConfig",
    "build_approval_keyboard",
    "build_session_key",
    "decode_approval_callback",
    "encode_approval_callback",
    "generate_approval_id",
    "load_config",
    "normalize_telegram_message",
    "parse_session_key",
]

