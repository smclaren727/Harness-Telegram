# Harness-Telegram Agent Guide

This repo is a standalone Python Telegram Bot API transport for
Emacs-Harness-style control planes. It owns Telegram long polling, inbound
normalization, session mapping, attachment import, inline approval callbacks,
and notification outbox delivery.

## Start Here

- Read this file, then read `README.md` before changing behavior.
- Read `pyproject.toml` before changing package metadata, dependencies, test
  tooling, or supported Python assumptions.
- Read the relevant tests under `tests/` before changing approvals, backend
  calls, config parsing, session behavior, Telegram update handling, or outbox
  delivery.

## Cross-Repo Boundaries

- `All-The-Things` owns live shared content, Org skills/workflows, memory,
  learnings, capture files, and knowledge.
- `Emacs-Harness` owns control-plane behavior, model routing, policy/action
  validation, workflow/skill execution, and the Elisp bridge consumed by this
  transport.
- `Nix-Emacs-Node` owns the live node service wiring, flake pin, Telegram token
  secret, runtime paths, and deployment.
- `Harness-Telegram` owns generic Telegram transport behavior and should stay
  reusable by Emacs-Harness-style backends.

Do not import Loxley-specific or personal Harness policy, memory, promotion,
deployment, drift, resource-command, or gateway logic into this package.

## Local Rules

- Keep the daemon generic and backend-oriented. Emacs-Harness-specific behavior
  belongs behind the configured backend/bridge boundary.
- Preserve HarnessResult-compatible reply, approval, and notification outbox
  behavior.
- Files without `chat_id` in the notification outbox should use the configured
  `operator_chat_id`.
- Successful outbox sends move to `sent/`; retryable failures remain pending
  until the configured attempt limit moves them to `failed/`.
- Do not commit generated runtime state, downloaded attachments, local config
  files with tokens, or auth material.

## Documentation And Hygiene

- Update docs in the same change when config fields, callback semantics,
  outbox behavior, deployment expectations, or backend contracts change.
- Keep changes scoped and covered by focused tests.
- Do not overwrite user work. If the tree is dirty, inspect the relevant files
  and preserve changes you did not make.
- Run the Python tests appropriate to the change before pushing. For normal
  behavior changes, run:

```bash
python -m pytest
```

## Maintaining This File

Update this file when an LLM or human would otherwise follow stale guidance.

Good reasons to update it:

- repo responsibilities or boundaries change
- required docs, test commands, deploy steps, or runtime paths change
- new recurring agent mistakes reveal missing guidance
- skills/workflows move locations or their authoring model changes
- security, secrets, policy, or mutation rules change

Do not update it for one-off task notes, temporary state, generated output, or
ordinary content changes that are already documented elsewhere.

## Deployment Notes

- Commit and push changes in this repo first.
- The live node consumes this package through the `harness-telegram` flake input
  in `Nix-Emacs-Node`.
- Node deployment requires repinning `Nix-Emacs-Node/flake.lock` to the pushed
  Harness-Telegram commit, then rebuilding the node.
- After deploys, verify `harness-telegram.service`, `emacs-node-daemon`, and a
  Telegram smoke path or health check when practical.
