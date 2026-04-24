"""Telegram Bot API adapter."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from harness_telegram.approvals import (
    PendingApprovalStore,
    build_approval_keyboard,
    decode_approval_callback,
)
from harness_telegram.session import build_session_key
from harness_telegram.types import (
    ApprovalDecision,
    ApprovalRequest,
    Attachment,
    HarnessResult,
    InboundMessage,
)

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LEN = 4096
REPLY_CONTEXT_LIMIT = 1024
NO_REPLY_SENTINEL = "NO_REPLY"

HandlerResult = HarnessResult | str | None


class TelegramAdapter:
    """Telegram Bot API long-polling adapter."""

    def __init__(
        self,
        *,
        token: str,
        default_agent_id: str = "default",
        bot_username: str = "",
        operator_chat_id: str = "",
        allowed_chat_ids: list[str] | None = None,
        import_root: str | Path = "",
        per_peer_direct_sessions: bool = False,
        approval_store: PendingApprovalStore | None = None,
    ) -> None:
        self._token = token
        self._default_agent_id = default_agent_id
        self._bot_username = bot_username
        self._operator_chat_id = str(operator_chat_id or "").strip()
        self._allowed_chat_ids = {str(item).strip() for item in allowed_chat_ids or [] if str(item).strip()}
        if self._operator_chat_id:
            self._allowed_chat_ids.add(self._operator_chat_id)
        self._base = f"https://api.telegram.org/bot{token}"
        self._file_base = f"https://api.telegram.org/file/bot{token}"
        self._import_root = Path(import_root) if import_root else Path.cwd() / "telegram-imports"
        self._per_peer_direct_sessions = per_peer_direct_sessions
        self._approval_store = approval_store or PendingApprovalStore()
        self._running = False
        self._reply_contexts: dict[tuple[str, str], tuple[str, str]] = {}
        self.on_approval: Callable[[ApprovalDecision], Awaitable[HandlerResult]] | None = None

    async def start_polling(
        self,
        on_message: Callable[[InboundMessage], Awaitable[HandlerResult]],
    ) -> None:
        self._running = True
        offset = 0
        async with httpx.AsyncClient(timeout=35.0) as client:
            while self._running:
                try:
                    response = await client.post(
                        f"{self._base}/getUpdates",
                        json={
                            "offset": offset,
                            "timeout": 30,
                            "limit": 100,
                            "allowed_updates": ["message", "callback_query"],
                        },
                    )
                    if response.status_code != 200:
                        logger.warning("Telegram getUpdates failed: %s", response.status_code)
                        await asyncio.sleep(5)
                        continue
                    for update in response.json().get("result", []):
                        offset = int(update["update_id"]) + 1
                        if update.get("callback_query") is not None:
                            await self._handle_update(update, on_message, client)
                        else:
                            asyncio.create_task(self._handle_update(update, on_message, client))
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning("Telegram polling error: %s", exc)
                    await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False

    async def _handle_update(
        self,
        update: dict[str, Any],
        on_message: Callable[[InboundMessage], Awaitable[HandlerResult]],
        client: httpx.AsyncClient,
    ) -> None:
        callback_query = update.get("callback_query")
        if callback_query is not None:
            await self._handle_callback_query(callback_query, client)
            return

        inbound = normalize_telegram_message(
            update,
            default_agent_id=self._default_agent_id,
            bot_username=self._bot_username,
            per_peer_mode=self._per_peer_direct_sessions,
        )
        if inbound is None or not self._chat_allowed(inbound.chat_id):
            return
        self._restore_reply_context(update, inbound)
        inbound.attachments = await self._download_attachments(inbound.attachments, client)
        result = await on_message(inbound)
        await self._send_handler_result(
            inbound.chat_id,
            result,
            agent_id=inbound.agent_id,
            session_key=inbound.session_key,
        )

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        *,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> bool:
        chunks = _split_message(_to_telegram_markdown(text), TELEGRAM_MAX_LEN)
        ok = True
        async with httpx.AsyncClient(timeout=30.0) as client:
            for chunk in chunks:
                response = await self._post_send_message(client, chat_id=chat_id, text=chunk)
                if response is None:
                    ok = False
                else:
                    self._remember_reply_context(
                        chat_id=chat_id,
                        response=response,
                        agent_id=agent_id,
                        session_key=session_key,
                    )
        return ok

    async def send_message_with_keyboard(
        self,
        chat_id: str | int,
        text: str,
        keyboard: dict[str, Any],
    ) -> bool:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await self._post_send_message(
                client,
                chat_id=chat_id,
                text=_to_telegram_markdown(text),
                reply_markup=keyboard,
            )
        return response is not None

    async def send_approval_request(self, chat_id: str | int, request: ApprovalRequest) -> bool:
        self._approval_store.register(request)
        return await self.send_message_with_keyboard(
            chat_id,
            request.summary_text(),
            build_approval_keyboard(request),
        )

    async def send_harness_result(
        self,
        chat_id: str | int,
        result: HandlerResult,
        *,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> bool:
        """Send a handler result to CHAT_ID and return whether all sends succeeded."""
        return await self._send_handler_result(
            chat_id,
            result,
            agent_id=agent_id,
            session_key=session_key,
        )

    async def _send_handler_result(
        self,
        chat_id: str | int,
        result: HandlerResult,
        *,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> bool:
        if result is None:
            return True
        approvals: list[ApprovalRequest] = []
        if isinstance(result, HarnessResult):
            text = result.operator_text()
            approvals = result.approval_requests
        else:
            text = str(result)
        ok = True
        if text and text.strip() != NO_REPLY_SENTINEL:
            ok = await self.send_message(
                chat_id,
                text,
                agent_id=agent_id,
                session_key=session_key,
            )
        for request in approvals:
            ok = await self.send_approval_request(chat_id, request) and ok
        return ok

    async def _post_send_message(
        self,
        client: httpx.AsyncClient,
        *,
        chat_id: str | int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> httpx.Response | None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            response = await client.post(
                f"{self._base}/sendMessage",
                json={**payload, "parse_mode": "Markdown"},
            )
            if response.status_code == 400 and "parse" in response.text.lower():
                response = await client.post(f"{self._base}/sendMessage", json=payload)
            if response.status_code != 200:
                logger.warning("Telegram sendMessage failed: %s %s", response.status_code, response.text[:200])
                return None
            return response
        except Exception as exc:
            logger.warning("Telegram sendMessage error: %s", exc)
            return None

    async def _handle_callback_query(self, callback_query: dict[str, Any], client: httpx.AsyncClient) -> None:
        data = str(callback_query.get("data") or "")
        decoded = decode_approval_callback(data)
        cq_id = str(callback_query.get("id") or "")
        message = callback_query.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id") or "")
        sender_id = str((callback_query.get("from") or {}).get("id") or "")
        message_id = str(message.get("message_id") or "") or None
        if not decoded:
            await self._answer_callback(client, cq_id, "Unknown action")
            return
        if chat_id and not self._chat_allowed(chat_id):
            await self._answer_callback(client, cq_id, "Not allowed")
            return

        approval_id, action = decoded
        pending = self._approval_store.get(approval_id)
        request = pending.request if pending is not None else None
        if pending is None:
            await self._answer_callback(client, cq_id, "Approval expired")
            return
        self._approval_store.resolve(approval_id, action)
        decision = ApprovalDecision(
            approval_id=approval_id,
            action=action,
            chat_id=chat_id,
            sender_id=sender_id,
            message_id=message_id,
            request=request,
            raw=callback_query,
        )
        result: HandlerResult = None
        if self.on_approval is not None:
            result = await self.on_approval(decision)
        await self._answer_callback(client, cq_id, _callback_answer_text(action))
        if chat_id:
            await self._send_handler_result(chat_id, result)

    async def _answer_callback(self, client: httpx.AsyncClient, callback_query_id: str, text: str) -> None:
        if not callback_query_id:
            return
        try:
            await client.post(
                f"{self._base}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text[:200]},
            )
        except Exception as exc:
            logger.debug("answerCallbackQuery failed: %s", exc)

    async def _download_attachments(
        self,
        attachments: list[Attachment],
        client: httpx.AsyncClient,
    ) -> list[Attachment]:
        if not attachments:
            return []
        self._import_root.mkdir(parents=True, exist_ok=True)
        resolved: list[Attachment] = []
        for index, attachment in enumerate(attachments, start=1):
            if not attachment.uri.startswith("tg://"):
                resolved.append(attachment)
                continue
            file_id = attachment.uri[len("tg://") :].strip()
            meta = await client.get(f"{self._base}/getFile", params={"file_id": file_id})
            meta.raise_for_status()
            file_path = str((meta.json().get("result") or {}).get("file_path") or "")
            if not file_path:
                raise RuntimeError(f"Telegram attachment could not be resolved: {file_id}")
            file_response = await client.get(f"{self._file_base}/{file_path}")
            file_response.raise_for_status()
            target = _next_available_import_path(
                self._import_root,
                _attachment_filename(attachment=attachment, file_path=file_path, index=index),
            )
            target.write_bytes(file_response.content)
            resolved.append(
                Attachment(
                    type=attachment.type,
                    uri=str(target),
                    name=target.name,
                    mime=attachment.mime or file_response.headers.get("content-type"),
                    size_bytes=attachment.size_bytes or len(file_response.content),
                )
            )
        return resolved

    def _chat_allowed(self, chat_id: str) -> bool:
        return not self._allowed_chat_ids or str(chat_id).strip() in self._allowed_chat_ids

    def _remember_reply_context(
        self,
        *,
        chat_id: str | int,
        response: httpx.Response,
        agent_id: str | None,
        session_key: str | None,
    ) -> None:
        if not agent_id or not session_key:
            return
        try:
            message_id = (response.json().get("result") or {}).get("message_id")
        except Exception:
            return
        if message_id is None:
            return
        self._reply_contexts[(str(chat_id), str(message_id))] = (agent_id, session_key)
        while len(self._reply_contexts) > REPLY_CONTEXT_LIMIT:
            self._reply_contexts.pop(next(iter(self._reply_contexts)), None)

    def _restore_reply_context(self, update: dict[str, Any], inbound: InboundMessage) -> None:
        message = update.get("message") or update.get("edited_message") or {}
        original_text = str(message.get("text") or message.get("caption") or "")
        if original_text.startswith("/") or not inbound.reply_to_message_id:
            return
        context = self._reply_contexts.get((inbound.chat_id, inbound.reply_to_message_id))
        if context is None:
            return
        inbound.agent_id, inbound.session_key = context


def normalize_telegram_message(
    update: dict[str, Any],
    *,
    default_agent_id: str = "default",
    bot_username: str = "",
    bot_user_id: int | None = None,
    per_peer_mode: bool = False,
) -> InboundMessage | None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return None
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    chat_type_raw = str(chat.get("type") or "private")
    chat_id = str(chat.get("id") or "")
    sender_id = str(from_user.get("id") or "")
    text = str(message.get("text") or message.get("caption") or "")
    message_id = str(message.get("message_id") or "")
    thread_id = str(message.get("message_thread_id") or "").strip() or None

    if chat_type_raw == "private":
        chat_type = "direct"
    elif chat_type_raw in {"group", "supergroup"}:
        chat_type = "group"
    else:
        chat_type = "channel"

    if chat_type in {"group", "channel"} and not _is_activated(
        message,
        text,
        bot_username,
        bot_user_id,
    ):
        return None

    agent_id = default_agent_id
    clean_text = text
    if text.startswith("/"):
        parts = text[1:].split(None, 1)
        candidate = parts[0].lower() if parts else ""
        if "@" in candidate:
            candidate = candidate.split("@", 1)[0]
        if candidate and candidate != "start" and len(candidate) > 1:
            agent_id = candidate
            clean_text = parts[1] if len(parts) > 1 else ""
        elif candidate == "start":
            clean_text = parts[1] if len(parts) > 1 else ""

    reply_to_id = None
    if message.get("reply_to_message"):
        reply_to_id = str((message["reply_to_message"] or {}).get("message_id") or "")

    session_key = build_session_key(
        agent_id=agent_id,
        channel="telegram",
        chat_type=chat_type,
        peer_id=chat_id,
        thread_id=thread_id,
        per_peer_mode=per_peer_mode,
    )
    return InboundMessage(
        sender_id=sender_id,
        sender_name=str(from_user.get("first_name") or from_user.get("username") or ""),
        chat_type=chat_type,  # type: ignore[arg-type]
        chat_id=chat_id,
        chat_title=str(chat.get("title") or ""),
        thread_id=thread_id,
        message_id=message_id,
        text=clean_text,
        attachments=_normalize_telegram_attachments(message),
        reply_to_message_id=reply_to_id,
        raw=update,
        session_key=session_key,
        agent_id=agent_id,
    )


def _normalize_telegram_attachments(message: dict[str, Any]) -> list[Attachment]:
    attachments: list[Attachment] = []
    document = message.get("document")
    if isinstance(document, dict):
        file_id = str(document.get("file_id") or "").strip()
        if file_id:
            attachments.append(
                Attachment(
                    type="file",
                    uri=f"tg://{file_id}",
                    name=str(document.get("file_name") or "").strip() or None,
                    mime=str(document.get("mime_type") or "").strip() or None,
                    size_bytes=document.get("file_size"),
                )
            )
    photos = message.get("photo") or []
    if isinstance(photos, list) and photos:
        best = max(photos, key=lambda item: int(item.get("file_size") or 0))
        file_id = str(best.get("file_id") or "").strip()
        if file_id:
            attachments.append(
                Attachment(
                    type="image",
                    uri=f"tg://{file_id}",
                    name=f"telegram-photo-{best.get('file_unique_id') or file_id}.jpg",
                    mime="image/jpeg",
                    size_bytes=best.get("file_size"),
                )
            )
    return attachments


def _is_activated(
    message: dict[str, Any],
    text: str,
    bot_username: str,
    bot_user_id: int | None,
) -> bool:
    if text.startswith("/"):
        return True
    normalized_bot_username = bot_username.lstrip("@").lower()
    if normalized_bot_username:
        for entity in message.get("entities") or []:
            if entity.get("type") != "mention":
                continue
            offset = int(entity.get("offset") or 0)
            length = int(entity.get("length") or 0)
            mentioned = text[offset : offset + length].strip().lstrip("@").lower()
            if mentioned == normalized_bot_username:
                return True
    reply_to = message.get("reply_to_message") or {}
    reply_from = reply_to.get("from") or {}
    return bool(
        reply_from.get("is_bot")
        and (bot_user_id is None or int(reply_from.get("id") or 0) == bot_user_id)
    )


def _to_telegram_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    return text


def _split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        split_at = remaining.rfind("\n\n", 0, max_len)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _attachment_filename(*, attachment: Attachment, file_path: str, index: int) -> str:
    raw_name = str(attachment.name or "").strip() or Path(file_path).name
    if not raw_name:
        guessed_ext = mimetypes.guess_extension(str(attachment.mime or "").strip())
        raw_name = f"telegram-file-{index}{guessed_ext or ''}"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-")
    return safe_name or f"telegram-file-{index}"


def _next_available_import_path(root: Path, filename: str) -> Path:
    target = root / filename
    if not target.exists():
        return target
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for idx in range(2, 1000):
        candidate = root / f"{stem}-{idx}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Unable to allocate import path for Telegram attachment {filename!r}")


def _callback_answer_text(action: str) -> str:
    return {
        "view": "Opening review",
        "approve": "Approved",
        "reject": "Rejected",
        "needs_revision": "Needs revision",
        "always_allow": "Always allowed",
    }.get(action, "Done")
