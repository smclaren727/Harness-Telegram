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

[emacs]
harness_root = "/tmp/harness"
socket_name = "emacs-node"
emacsclient = "emacsclient-test"
batch_fallback = false
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
    assert cfg.emacs.socket_name == "emacs-node"
    assert cfg.emacs.batch_fallback is False

