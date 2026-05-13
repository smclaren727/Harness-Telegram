# Harness Telegram

`Harness Telegram` is a standalone Telegram Bot API transport for
Emacs-Harness-style control planes. It uses long polling, normalizes Telegram
updates into channel-neutral message objects, supports Telegram forum topics as
durable session threads, and provides inline approval buttons.

The package is intentionally separate from Emacs-Harness. The Telegram daemon
talks to configured Python backends. The default Emacs-Harness backend calls
the harness-supplied `Lisp/harness-telegram-bridge.el` through `emacsclient`,
leaving Emacs-Harness as the control plane. A generic process backend can route
Telegram chats or topics to local commands such as `codex` or `claude` without
requiring an Emacs-Harness checkout.

This repository intentionally contains only Python package/supporting code. The
Elisp bridge lives in the control-plane repo that consumes the package.

## Usage

```bash
harness-telegram --config /path/to/config.toml
```

Example config:

```toml
[backend]
type = "emacs"

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

## Process Backend

Use the process backend when Telegram should dispatch directly to local command
line tools instead of an Emacs-Harness bridge:

```toml
[backend]
type = "process"

[telegram]
token_env = "TELEGRAM_BOT_TOKEN"
operator_chat_id = "123456"
allowed_chat_ids = ["123456", "-100100200300"]
import_root = "/srv/loxley/.local/share/harness-telegram/imports"
activate_all_messages = true

[process]
default_agent = "codex"
state_root = "/srv/loxley/.local/state/harness-telegram"
timeout_seconds = 1800
max_reply_chars = 12000

[[process.routes]]
chat_id = "-100100200300"
thread_id = "77"
agent = "claude"

[process.agents.codex]
command = ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "{text}"]
cwd = "/srv/loxley"
input_mode = "none"
env = { CODEX_HOME = "/srv/loxley/.codex" }

[process.agents.claude]
command = ["claude", "--print", "{text}"]
cwd = "/srv/loxley"
input_mode = "none"
```

The backend also exports session metadata to the child process:
`HARNESS_TELEGRAM_AGENT_ID`, `HARNESS_TELEGRAM_SESSION_KEY`,
`HARNESS_TELEGRAM_SESSION_DIR`, `HARNESS_TELEGRAM_CHAT_ID`,
`HARNESS_TELEGRAM_THREAD_ID`, `HARNESS_TELEGRAM_MESSAGE_ID`, and
`HARNESS_TELEGRAM_ATTACHMENTS_JSON`. If `input_mode = "stdin"`, the message text
and attachment paths are sent on stdin instead of requiring a `{text}` argument.
Set `telegram.activate_all_messages = true` for dedicated groups or forum topics
where every message should be treated as agent input without requiring a slash
command, bot mention, or reply to a bot message.

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
