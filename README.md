# Harness Telegram

`Harness Telegram` is a standalone Telegram Bot API transport for
Emacs-Harness-style control planes. It uses long polling, normalizes Telegram
updates into channel-neutral message objects, supports Telegram forum topics as
durable session threads, and provides inline approval buttons.

The package is intentionally separate from Emacs-Harness. The Telegram daemon
talks to configured Python backends; for Emacs-Harness, the backend calls the
harness-supplied `Lisp/harness-telegram-bridge.el` through `emacsclient`,
leaving Emacs-Harness as the control plane.

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

[emacs]
harness_root = "/srv/emacs-node/Harness"
socket_name = "/srv/emacs-node/.emacs.d/var/server/emacs-node"
emacsclient = "emacsclient"
batch_fallback = true
```

## Borrowed Shape

The implementation is adapted from reusable patterns in Loxley-Harness:
Telegram long polling, inbound normalization, markdown-safe chunking, inline
callback buttons, approval stores, and canonical session keys. It does not
import Loxley or carry over Loxley-specific memory, deploy, promotion, drift,
resource-command, or gateway logic.
