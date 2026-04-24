"""File-backed notification outbox for Emacs-Harness."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from harness_telegram.backend import extract_workflow_approval_requests
from harness_telegram.telegram import TelegramAdapter
from harness_telegram.types import HarnessResult

logger = logging.getLogger(__name__)


class HarnessTelegramOutbox:
    """Poll and deliver HarnessResult-compatible notification files."""

    def __init__(
        self,
        *,
        harness_root: str | Path,
        adapter: TelegramAdapter,
        operator_chat_id: str = "",
        poll_interval_seconds: float = 5.0,
        max_attempts: int = 3,
    ) -> None:
        self.harness_root = Path(harness_root).expanduser().resolve()
        self.adapter = adapter
        self.operator_chat_id = str(operator_chat_id or "").strip()
        self.poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.outbox_root = (
            self.harness_root / "Runtime" / "State" / "Harness-Telegram" / "Outbox"
        )
        self.pending_dir = self.outbox_root / "pending"
        self.sent_dir = self.outbox_root / "sent"
        self.failed_dir = self.outbox_root / "failed"

    async def run(self, stop_event: asyncio.Event) -> None:
        """Poll until STOP_EVENT is set."""
        while not stop_event.is_set():
            await self.process_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                pass

    async def process_once(self) -> list[Path]:
        """Process currently pending notifications and return final paths touched."""
        self._ensure_dirs()
        destinations: list[Path] = []
        for path in sorted(self.pending_dir.glob("*.json")):
            destination = await self._process_path(path)
            if destination is not None:
                destinations.append(destination)
        return destinations

    async def _process_path(self, path: Path) -> Path | None:
        try:
            payload = self._read_payload(path)
            chat_id = str(payload.get("chat_id") or self.operator_chat_id).strip()
            if not chat_id:
                raise OutboxDeliveryError("notification has no chat_id and no operator_chat_id")
            result = HarnessResult.from_payload(payload)
            if not result.approval_requests:
                result.approval_requests = extract_workflow_approval_requests(result)
            ok = await self.adapter.send_harness_result(
                chat_id,
                result,
                agent_id=str(payload.get("agent_id") or "") or None,
                session_key=str(payload.get("session_key") or "") or None,
            )
            if not ok:
                raise OutboxDeliveryError("Telegram send failed")
            return self._move(path, self.sent_dir)
        except Exception as exc:
            logger.warning("Outbox delivery failed for %s: %s", path, exc)
            return self._record_failure(path, exc)

    def _read_payload(self, path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise OutboxDeliveryError("notification payload must be a JSON object")
        return payload

    def _record_failure(self, path: Path, exc: Exception) -> Path | None:
        try:
            payload = self._read_payload(path)
        except Exception:
            return self._move(path, self.failed_dir)

        attempts = int(payload.get("_outbox_attempts") or 0) + 1
        payload["_outbox_attempts"] = attempts
        payload["_last_error"] = str(exc)
        if attempts >= self.max_attempts:
            self._write_json(path, payload)
            return self._move(path, self.failed_dir)
        self._write_json(path, payload)
        return None

    def _move(self, path: Path, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        destination = _next_available_path(directory / path.name)
        path.replace(destination)
        return destination

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def _ensure_dirs(self) -> None:
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.sent_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)


class OutboxDeliveryError(RuntimeError):
    """Raised when an outbox notification cannot be delivered."""


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise OutboxDeliveryError(f"could not allocate destination path for {path}")
