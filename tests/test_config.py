from __future__ import annotations

from harness_telegram.config import load_config


def test_load_config(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[telegram]
token_env = "TEST_TELEGRAM_TOKEN"
bot_username = "harness_bot"
operator_chat_id = "123"
allowed_chat_ids = ["123", "-1001"]
per_peer_direct_sessions = true
activate_all_messages = true

[emacs]
harness_root = "/tmp/harness"
socket_name = "emacs-node"
emacsclient = "emacsclient-test"
batch_fallback = false

[backend]
type = "process"

[process]
default_agent = "codex"
state_root = "/srv/loxley/.local/state/harness-telegram"
timeout_seconds = 45
max_reply_chars = 8000
include_stderr = true

[[process.routes]]
chat_id = "-1001"
thread_id = "77"
agent = "claude"

[process.agents.codex]
command = ["codex", "exec", "{text}"]
cwd = "/srv/loxley"
input_mode = "none"
env = { CODEX_HOME = "/srv/loxley/.codex" }

[process.agents.claude]
command = "claude --print"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_TELEGRAM_TOKEN", "secret-token")

    cfg = load_config(config)

    assert cfg.telegram.token == "secret-token"
    assert cfg.telegram.bot_username == "harness_bot"
    assert cfg.telegram.allowed_chat_ids == ["123", "-1001"]
    assert cfg.telegram.import_root == "/tmp/harness/Runtime/Imports/Telegram"
    assert cfg.telegram.per_peer_direct_sessions is True
    assert cfg.telegram.activate_all_messages is True
    assert cfg.emacs.socket_name == "emacs-node"
    assert cfg.emacs.batch_fallback is False
    assert cfg.backend.type == "process"
    assert cfg.process.default_agent == "codex"
    assert cfg.process.timeout_seconds == 45
    assert cfg.process.max_reply_chars == 8000
    assert cfg.process.include_stderr is True
    assert cfg.process.routes[0].agent == "claude"
    assert cfg.process.routes[0].thread_id == "77"
    assert cfg.process.agents["codex"].command == ["codex", "exec", "{text}"]
    assert cfg.process.agents["codex"].env["CODEX_HOME"] == "/srv/loxley/.codex"
    assert cfg.process.agents["claude"].command == ["claude", "--print"]
