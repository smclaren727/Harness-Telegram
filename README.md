# Harness Telegram

`Harness Telegram` is a standalone Telegram Bot API transport for
Emacs-Harness-style control planes. It uses long polling, normalizes Telegram
updates into channel-neutral message objects, supports Telegram forum topics as
durable session threads, and provides inline approval buttons.

The package is intentionally separate from Emacs-Harness. The Telegram daemon
talks to configured Python backends; for Emacs-Harness, the backend calls the
harness-supplied `Lisp/harness-telegram-bridge.el` through `emacsclient`,
leaving Emacs-Harness as the control plane.

This repository intentionally contains only Python package/supporting code. The
Elisp bridge lives in the control-plane repo that consumes the package.

## Usage

```bash
harness-telegram --config /path/to/config.toml
```

Example config:

```toml
[telegram]
token_env = "TELEGRAM_BOT_TOKEN"
bot_username = "your_bot"
operator_chat_id = "123456"
allowed_chat_ids = ["123456"]
import_root = "/srv/emacs-node/Harness/Runtime/Imports/Telegram"
per_peer_direct_sessions = false
outbox_poll_interval_seconds = 5
outbox_max_attempts = 3

[emacs]
harness_root = "/srv/emacs-node/Harness"
socket_name = "/srv/emacs-node/.emacs.d/var/server/emacs-node"
emacsclient = "emacsclient"
batch_fallback = true
bridge_elisp = "/srv/emacs-node/Harness/Lisp/harness-telegram-bridge.el"
```

When `emacs.harness_root` is set, the daemon also polls
`Runtime/State/Harness-Telegram/Outbox/pending/` for HarnessResult-compatible
notification JSON. Files without `chat_id` are sent to `operator_chat_id`;
successful sends move to `sent/`, and repeated failures move to `failed/`.

## Nix Node Deployment

The live `loxley` node consumes this package through the `harness-telegram`
flake input in `Nix-Emacs-Node`. The Nix module builds the package as a Python
application, starts `harness-telegram.service`, exports the Telegram bot token
from `/run/secrets/telegram_bot_token`, and sets
`EH_TELEGRAM_TRANSPORT=external` on the Emacs daemon so telega is not started
for live harness traffic.

Routine housekeeping commits in this repo do not affect the node until
`Nix-Emacs-Node/flake.lock` is repinned.

## Borrowed Shape

The implementation is adapted from reusable patterns in Loxley-Harness:
Telegram long polling, inbound normalization, markdown-safe chunking, inline
callback buttons, approval stores, and canonical session keys. It does not
import Loxley or carry over Loxley-specific memory, deploy, promotion, drift,
resource-command, or gateway logic.
