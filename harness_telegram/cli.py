"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from harness_telegram.backend import EmacsHarnessBackend
from harness_telegram.config import load_config
from harness_telegram.telegram import TelegramAdapter


async def amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Harness Telegram daemon")
    parser.add_argument("--config", required=True, help="Path to config.toml")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    cfg = load_config(args.config)
    if not cfg.telegram.token:
        raise SystemExit(f"Telegram token env var is empty: {cfg.telegram.token_env}")

    backend = EmacsHarnessBackend(cfg.emacs)
    adapter = TelegramAdapter(
        token=cfg.telegram.token,
        default_agent_id="default",
        bot_username=cfg.telegram.bot_username,
        operator_chat_id=cfg.telegram.operator_chat_id,
        allowed_chat_ids=cfg.telegram.allowed_chat_ids,
        import_root=cfg.telegram.import_root,
        per_peer_direct_sessions=cfg.telegram.per_peer_direct_sessions,
    )
    adapter.on_approval = backend.handle_approval

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    task = asyncio.create_task(adapter.start_polling(backend.handle_message))
    await stop_event.wait()
    adapter.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(asyncio.run(amain(argv)))


if __name__ == "__main__":
    main()
