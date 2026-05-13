"""Backend protocol and bridge implementations."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol

from harness_telegram.config import (
    EmacsConfig,
    HarnessTelegramConfig,
    ProcessAgentConfig,
    ProcessConfig,
)
from harness_telegram.types import (
    ApprovalButton,
    ApprovalDecision,
    ApprovalRequest,
    HarnessEvent,
    HarnessResult,
    InboundMessage,
)


class Backend(Protocol):
    def handle_message(self, inbound: InboundMessage) -> Awaitable[HarnessResult]: ...
    def handle_approval(self, decision: ApprovalDecision) -> Awaitable[HarnessResult | None]: ...


def build_backend(config: HarnessTelegramConfig) -> Backend:
    backend_type = config.backend.type.strip().lower()
    if backend_type == "emacs":
        return EmacsHarnessBackend(config.emacs)
    if backend_type == "process":
        return ProcessBackend(config.process)
    raise ValueError(f"Unknown backend type: {config.backend.type!r}")


class EmacsHarnessBackend:
    """Backend that dispatches Telegram events into Emacs-Harness."""

    def __init__(self, config: EmacsConfig) -> None:
        self.config = config
        if not self.config.harness_root:
            raise ValueError("emacs.harness_root is required")
        self.harness_root = Path(self.config.harness_root).expanduser().resolve()

    async def handle_message(self, inbound: InboundMessage) -> HarnessResult:
        event = self.build_event(inbound)
        return await self._run_event(event)

    async def handle_approval(self, decision: ApprovalDecision) -> HarnessResult | None:
        request = decision.request
        if request is None:
            return None
        event_text = request.event_text_for(decision.action)
        if not event_text:
            return HarnessResult(
                status="ignored",
                reply=f"No harness event is mapped for approval action {decision.action}.",
            )
        event = HarnessEvent(
            event_id=f"telegram-approval-{decision.approval_id}-{decision.action}",
            trigger="telegram",
            text=event_text,
            source_chat_id=decision.chat_id,
            source_chat_title="",
            source_message_id=decision.message_id or f"callback-{decision.approval_id}",
            sender_title=decision.sender_id,
            session_key=f"approval:{decision.approval_id}",
        )
        self._apply_listener_defaults(event)
        return await self._run_event(event)

    def build_event(self, inbound: InboundMessage) -> HarnessEvent:
        thread_part = f"-{inbound.thread_id}" if inbound.thread_id else ""
        event = HarnessEvent(
            event_id=f"telegram-{inbound.chat_id}{thread_part}-{inbound.message_id}",
            text=inbound.text.strip(),
            source_chat_id=inbound.chat_id,
            source_chat_title=inbound.chat_title,
            source_message_id=inbound.message_id,
            source_thread_id=inbound.thread_id,
            sender_title=inbound.sender_name,
            session_key=inbound.session_key,
            attachments=inbound.attachments,
        )
        self._apply_listener_defaults(event)
        return event

    def _apply_listener_defaults(self, event: HarnessEvent) -> None:
        listener = self._default_telegram_listener()
        if not listener:
            return
        event.listener = str(listener.get("name") or event.listener or "")
        event.route = str(listener.get("route") or event.route or "")
        provider = listener.get("provider")
        if provider:
            event.provider = str(provider)
        context_files = listener.get("context_files")
        if isinstance(context_files, list):
            event.context_files = [str(item) for item in context_files]

    def _default_telegram_listener(self) -> dict | None:
        path = self.harness_root / "Config" / "listeners.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        selected = payload.get("default_telegram_listener")
        listeners = payload.get("listeners") or []
        for listener in listeners:
            if listener.get("name") == selected and listener.get("type") == "telegram":
                return listener
        return None

    async def _run_event(self, event: HarnessEvent) -> HarnessResult:
        event_path = self._write_event_file(event)
        payload = await asyncio.to_thread(self._run_event_file, event_path)
        result = HarnessResult.from_payload(payload)
        if not result.approval_requests:
            result.approval_requests = extract_workflow_approval_requests(result)
        return result

    def _write_event_file(self, event: HarnessEvent) -> Path:
        state_dir = self.harness_root / "Runtime" / "State" / "Harness-Telegram"
        state_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", event.event_id).strip("-")
        path = state_dir / f"{safe_id or 'telegram-event'}.json"
        path.write_text(
            json.dumps(event.to_json_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def _run_event_file(self, event_path: Path) -> dict:
        try:
            return self._run_with_emacsclient(event_path)
        except Exception:
            if not self.config.batch_fallback:
                raise
            return self._run_with_batch_fallback(event_path)

    def _run_with_emacsclient(self, event_path: Path) -> dict:
        expression = (
            "(progn "
            f"(load-file {_elisp_quote(str(self._bridge_path()))}) "
            f"(harness-telegram-bridge-run-event-file {_elisp_quote(str(event_path))} "
            f"{_elisp_quote(str(self.harness_root))}))"
        )
        command = [self.config.emacsclient]
        if self.config.socket_name:
            command.extend(["-s", self.config.socket_name])
        command.extend(["--eval", expression])
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=self.config.timeout_seconds,
        )
        return _decode_eval_json(completed.stdout)

    def _run_with_batch_fallback(self, event_path: Path) -> dict:
        command = [
            self.config.emacs,
            "-Q",
            "--batch",
            "-L",
            str(self.harness_root / "Lisp"),
            "-l",
            str(self.harness_root / "Lisp" / "eh-cli.el"),
            "-f",
            "eh-cli-main",
            str(event_path),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.config.timeout_seconds,
        )
        if completed.returncode != 0:
            return {
                "status": "error",
                "error": (completed.stderr or completed.stdout).strip(),
            }
        return {
            "status": "success",
            "reply": completed.stdout.strip(),
        }

    def _bridge_path(self) -> Path:
        if self.config.bridge_elisp:
            return Path(self.config.bridge_elisp).expanduser().resolve()
        return self.harness_root / "Lisp" / "harness-telegram-bridge.el"


class ProcessBackend:
    """Backend that dispatches Telegram messages to configured local commands."""

    def __init__(self, config: ProcessConfig) -> None:
        self.config = config
        self.agents = {key.lower(): value for key, value in config.agents.items()}
        if not self.agents:
            raise ValueError("process.agents must define at least one agent")
        self.default_agent = config.default_agent.strip().lower()
        if self.default_agent not in self.agents:
            if len(self.agents) == 1:
                self.default_agent = next(iter(self.agents))
            else:
                raise ValueError(
                    f"process.default_agent is not configured: {config.default_agent!r}"
                )
        self.state_root = Path(config.state_root or ".harness-telegram/process").expanduser()

    async def handle_message(self, inbound: InboundMessage) -> HarnessResult:
        agent_name, agent = self._select_agent(inbound)
        if not agent.command:
            return HarnessResult(
                status="error",
                error=f"Process agent {agent_name!r} has no command configured.",
            )

        session_key = inbound.session_key or f"agent:{agent_name}:main"
        session_dir = self._session_dir(session_key)
        session_dir.mkdir(parents=True, exist_ok=True)
        prompt = _prompt_for_inbound(inbound)
        self._append_session_event(
            session_dir,
            {
                "role": "user",
                "agent": agent_name,
                "text": inbound.text,
                "chat_id": inbound.chat_id,
                "thread_id": inbound.thread_id,
                "message_id": inbound.message_id,
                "attachments": [attachment.model_dump() for attachment in inbound.attachments],
            },
        )

        payload = await asyncio.to_thread(
            self._run_process,
            agent_name,
            agent,
            inbound,
            prompt,
            session_dir,
        )
        self._append_session_event(
            session_dir,
            {
                "role": "assistant",
                "agent": agent_name,
                "status": payload.status,
                "reply": payload.reply,
                "error": payload.error,
            },
        )
        return payload

    async def handle_approval(self, decision: ApprovalDecision) -> HarnessResult | None:
        return HarnessResult(
            status="ignored",
            reply=f"Process backend does not handle approval action {decision.action}.",
        )

    def _select_agent(self, inbound: InboundMessage) -> tuple[str, ProcessAgentConfig]:
        for route in self.config.routes:
            if route.chat_id and route.chat_id != inbound.chat_id:
                continue
            if route.thread_id and route.thread_id != str(inbound.thread_id or ""):
                continue
            agent_name = route.agent.strip().lower()
            if agent_name in self.agents:
                return agent_name, self.agents[agent_name]

        agent_name = inbound.agent_id.strip().lower()
        if agent_name in self.agents:
            return agent_name, self.agents[agent_name]
        return self.default_agent, self.agents[self.default_agent]

    def _session_dir(self, session_key: str) -> Path:
        safe_key = re.sub(r"[^A-Za-z0-9._=-]+", "-", session_key).strip("-")
        return self.state_root / "sessions" / (safe_key or "default")

    def _run_process(
        self,
        agent_name: str,
        agent: ProcessAgentConfig,
        inbound: InboundMessage,
        prompt: str,
        session_dir: Path,
    ) -> HarnessResult:
        command = [_format_template(arg, inbound, agent_name, session_dir) for arg in agent.command]
        env = os.environ.copy()
        env.update(agent.env)
        env.update(_process_env(inbound, agent_name, session_dir))
        timeout = agent.timeout_seconds or self.config.timeout_seconds
        cwd = str(Path(agent.cwd).expanduser()) if agent.cwd else None
        input_text = prompt if agent.input_mode == "stdin" else None

        try:
            completed = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return HarnessResult(
                status="error",
                error=f"Process agent {agent_name!r} timed out after {timeout:g}s.",
            )
        except OSError as exc:
            return HarnessResult(
                status="error",
                error=f"Process agent {agent_name!r} failed to start: {exc}",
            )

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        max_reply_chars = agent.max_reply_chars or self.config.max_reply_chars
        if completed.returncode == 0:
            reply = stdout or f"Process agent {agent_name!r} completed with no output."
            if self.config.include_stderr and stderr:
                reply = f"{reply}\n\n[stderr]\n{stderr}" if reply else stderr
            return HarnessResult(
                status="success",
                reply=_truncate_reply(reply, max_reply_chars),
                raw={"agent": agent_name, "returncode": completed.returncode},
            )

        error_lines = [f"Process agent {agent_name!r} exited with status {completed.returncode}."]
        if stdout:
            error_lines.extend(["", "[stdout]", stdout])
        if stderr:
            error_lines.extend(["", "[stderr]", stderr])
        return HarnessResult(
            status="error",
            error=_truncate_reply("\n".join(error_lines), max_reply_chars),
            raw={"agent": agent_name, "returncode": completed.returncode},
        )

    def _append_session_event(self, session_dir: Path, payload: dict) -> None:
        path = session_dir / "events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def extract_workflow_approval_requests(result: HarnessResult) -> list[ApprovalRequest]:
    reply = result.reply or ""
    run_id = str(result.raw.get("workflow_run_id") or "").strip()
    if not run_id:
        match = re.search(r"\bworkflow-approve:\s*([^\s]+)", reply)
        if match:
            run_id = match.group(1)
    if not run_id:
        return []
    if "workflow-approve:" not in reply or "workflow-deny:" not in reply:
        return []
    body_lines = []
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped.startswith("workflow-"):
            continue
        if stripped == "Review commands:":
            continue
        body_lines.append(line)
    return [
        ApprovalRequest(
            approval_id=f"workflow-{run_id}",
            title="Workflow review",
            body="\n".join(body_lines).strip(),
            kind="workflow",
            run_id=run_id,
            event_text_by_action={
                "view": f"workflow-view: {run_id}",
                "approve": f"workflow-approve: {run_id}",
                "reject": f"workflow-deny: {run_id}",
            },
            buttons=[
                ApprovalButton(label="View", action="view"),
                ApprovalButton(label="Approve", action="approve"),
                ApprovalButton(label="Reject", action="reject"),
            ],
        )
    ]


def _elisp_quote(value: str) -> str:
    return json.dumps(value)


def _decode_eval_json(stdout: str) -> dict:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("emacsclient produced no JSON output")
    text = lines[-1]
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = text
    if isinstance(decoded, str):
        return json.loads(decoded)
    if isinstance(decoded, dict):
        return decoded
    raise ValueError("emacsclient output did not decode to a JSON object")


def _prompt_for_inbound(inbound: InboundMessage) -> str:
    parts = [inbound.text.strip()]
    if inbound.attachments:
        parts.append("")
        parts.append("Attachments:")
        for attachment in inbound.attachments:
            label = attachment.name or attachment.type
            parts.append(f"- {label}: {attachment.uri}")
    return "\n".join(part for part in parts if part is not None)


def _format_template(
    value: str,
    inbound: InboundMessage,
    agent_name: str,
    session_dir: Path,
) -> str:
    values = {
        "agent": agent_name,
        "agent_id": agent_name,
        "chat_id": inbound.chat_id,
        "message_id": inbound.message_id,
        "session_dir": str(session_dir),
        "session_key": inbound.session_key,
        "text": inbound.text,
        "thread_id": inbound.thread_id or "",
    }
    return value.format_map(_DefaultFormatValues(values))


def _process_env(inbound: InboundMessage, agent_name: str, session_dir: Path) -> dict[str, str]:
    attachments_json = json.dumps(
        [attachment.model_dump() for attachment in inbound.attachments],
        ensure_ascii=False,
    )
    return {
        "HARNESS_TELEGRAM_AGENT_ID": agent_name,
        "HARNESS_TELEGRAM_ATTACHMENTS_JSON": attachments_json,
        "HARNESS_TELEGRAM_CHAT_ID": inbound.chat_id,
        "HARNESS_TELEGRAM_MESSAGE_ID": inbound.message_id,
        "HARNESS_TELEGRAM_SESSION_DIR": str(session_dir),
        "HARNESS_TELEGRAM_SESSION_KEY": inbound.session_key,
        "HARNESS_TELEGRAM_THREAD_ID": inbound.thread_id or "",
    }


def _truncate_reply(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = "\n\n[truncated]"
    return text[: max(0, max_chars - len(marker))].rstrip() + marker


class _DefaultFormatValues(dict):
    def __missing__(self, key: str) -> str:
        return ""
