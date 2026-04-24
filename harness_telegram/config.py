"""TOML configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.11+ has tomllib
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class TelegramConfig:
    token_env: str = "TELEGRAM_BOT_TOKEN"
    bot_username: str = ""
    operator_chat_id: str = ""
    allowed_chat_ids: list[str] = field(default_factory=list)
    import_root: str = ""
    per_peer_direct_sessions: bool = False
    outbox_poll_interval_seconds: float = 5.0
    outbox_max_attempts: int = 3

    @property
    def token(self) -> str:
        return os.environ.get(self.token_env, "")


@dataclass
class EmacsConfig:
    harness_root: str = ""
    socket_name: str = ""
    emacsclient: str = "emacsclient"
    batch_fallback: bool = True
    emacs: str = "emacs"
    bridge_elisp: str = ""
    timeout_seconds: float = 180.0


@dataclass
class HarnessTelegramConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    emacs: EmacsConfig = field(default_factory=EmacsConfig)


def load_config(path: str | Path) -> HarnessTelegramConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    cfg = HarnessTelegramConfig()
    telegram = data.get("telegram", {})
    if telegram:
        cfg.telegram.token_env = str(telegram.get("token_env", cfg.telegram.token_env))
        cfg.telegram.bot_username = str(telegram.get("bot_username", cfg.telegram.bot_username))
        cfg.telegram.operator_chat_id = str(
            telegram.get("operator_chat_id", cfg.telegram.operator_chat_id)
        )
        cfg.telegram.allowed_chat_ids = [
            str(item).strip()
            for item in telegram.get("allowed_chat_ids", cfg.telegram.allowed_chat_ids)
            if str(item).strip()
        ]
        cfg.telegram.import_root = str(telegram.get("import_root", cfg.telegram.import_root))
        cfg.telegram.per_peer_direct_sessions = bool(
            telegram.get(
                "per_peer_direct_sessions",
                cfg.telegram.per_peer_direct_sessions,
            )
        )
        cfg.telegram.outbox_poll_interval_seconds = float(
            telegram.get(
                "outbox_poll_interval_seconds",
                cfg.telegram.outbox_poll_interval_seconds,
            )
        )
        cfg.telegram.outbox_max_attempts = int(
            telegram.get("outbox_max_attempts", cfg.telegram.outbox_max_attempts)
        )

    emacs = data.get("emacs", {})
    if emacs:
        cfg.emacs.harness_root = str(emacs.get("harness_root", cfg.emacs.harness_root))
        cfg.emacs.socket_name = str(emacs.get("socket_name", cfg.emacs.socket_name))
        cfg.emacs.emacsclient = str(emacs.get("emacsclient", cfg.emacs.emacsclient))
        cfg.emacs.batch_fallback = bool(emacs.get("batch_fallback", cfg.emacs.batch_fallback))
        cfg.emacs.emacs = str(emacs.get("emacs", cfg.emacs.emacs))
        cfg.emacs.bridge_elisp = str(emacs.get("bridge_elisp", cfg.emacs.bridge_elisp))
        cfg.emacs.timeout_seconds = float(
            emacs.get("timeout_seconds", cfg.emacs.timeout_seconds)
        )

    if not cfg.telegram.import_root and cfg.emacs.harness_root:
        cfg.telegram.import_root = str(
            Path(cfg.emacs.harness_root) / "Runtime" / "Imports" / "Telegram"
        )
    return cfg
