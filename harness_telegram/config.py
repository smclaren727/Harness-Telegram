"""TOML configuration loading."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.11+ has tomllib
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class BackendConfig:
    type: str = "emacs"


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
class ProcessAgentConfig:
    command: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    input_mode: str = "stdin"
    timeout_seconds: float | None = None
    max_reply_chars: int | None = None


@dataclass
class ProcessRouteConfig:
    agent: str
    chat_id: str = ""
    thread_id: str = ""


@dataclass
class ProcessConfig:
    default_agent: str = "default"
    state_root: str = ""
    timeout_seconds: float = 1800.0
    max_reply_chars: int = 12000
    include_stderr: bool = False
    agents: dict[str, ProcessAgentConfig] = field(default_factory=dict)
    routes: list[ProcessRouteConfig] = field(default_factory=list)


@dataclass
class HarnessTelegramConfig:
    backend: BackendConfig = field(default_factory=BackendConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    emacs: EmacsConfig = field(default_factory=EmacsConfig)
    process: ProcessConfig = field(default_factory=ProcessConfig)


def load_config(path: str | Path) -> HarnessTelegramConfig:
    config_path = Path(path)
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    cfg = HarnessTelegramConfig()
    backend = data.get("backend", {})
    if backend:
        cfg.backend.type = str(backend.get("type", cfg.backend.type)).strip().lower() or "emacs"

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

    process = data.get("process", {})
    if process:
        cfg.process.default_agent = str(
            process.get("default_agent", cfg.process.default_agent)
        ).strip() or cfg.process.default_agent
        cfg.process.state_root = str(process.get("state_root", cfg.process.state_root))
        cfg.process.timeout_seconds = float(
            process.get("timeout_seconds", cfg.process.timeout_seconds)
        )
        cfg.process.max_reply_chars = int(
            process.get("max_reply_chars", cfg.process.max_reply_chars)
        )
        cfg.process.include_stderr = bool(
            process.get("include_stderr", cfg.process.include_stderr)
        )
        cfg.process.agents = _load_process_agents(process.get("agents", {}))
        cfg.process.routes = _load_process_routes(process.get("routes", []))

    if not cfg.telegram.import_root and cfg.emacs.harness_root:
        cfg.telegram.import_root = str(
            Path(cfg.emacs.harness_root) / "Runtime" / "Imports" / "Telegram"
        )
    return cfg


def _load_process_agents(raw_agents: object) -> dict[str, ProcessAgentConfig]:
    if not isinstance(raw_agents, dict):
        return {}
    agents: dict[str, ProcessAgentConfig] = {}
    for raw_name, raw_payload in raw_agents.items():
        name = str(raw_name).strip().lower()
        if not name or not isinstance(raw_payload, dict):
            continue
        command = _load_command(raw_payload.get("command", []))
        env = raw_payload.get("env", {})
        if not isinstance(env, dict):
            env = {}
        timeout = raw_payload.get("timeout_seconds")
        max_reply_chars = raw_payload.get("max_reply_chars")
        agents[name] = ProcessAgentConfig(
            command=command,
            cwd=str(raw_payload.get("cwd", "")),
            env={str(key): str(value) for key, value in env.items()},
            input_mode=str(raw_payload.get("input_mode", "stdin")).strip().lower() or "stdin",
            timeout_seconds=float(timeout) if timeout is not None else None,
            max_reply_chars=int(max_reply_chars) if max_reply_chars is not None else None,
        )
    return agents


def _load_process_routes(raw_routes: object) -> list[ProcessRouteConfig]:
    if not isinstance(raw_routes, list):
        return []
    routes: list[ProcessRouteConfig] = []
    for item in raw_routes:
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent", "")).strip().lower()
        if not agent:
            continue
        routes.append(
            ProcessRouteConfig(
                agent=agent,
                chat_id=str(item.get("chat_id", "")).strip(),
                thread_id=str(item.get("thread_id", "")).strip(),
            )
        )
    return routes


def _load_command(raw_command: object) -> list[str]:
    if isinstance(raw_command, str):
        return shlex.split(raw_command)
    if isinstance(raw_command, list):
        return [str(item) for item in raw_command]
    return []
