# AGENTS.md — guide for AI agents contributing to ai-vlc

This file is for AI coding agents that read, test, or change this project. The human-facing rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## What this project is

`ai-vlc` controls a background VLC process through its remote-control socket. Its job is to make VLC predictable for an agent: every command either confirms its effect or reports that it could not.

## Promises to keep

1. **Confirm, do not assume.** A command that changes playback must check VLC's reply. If VLC does not confirm in time, report it as unconfirmed (`RCTimeoutError`), never as success.
2. **Clear errors on stderr.** Failures print `error: ...` to stderr and return a non-zero exit code.
3. **Local only.** The RC socket is bound to 127.0.0.1. Do not add network listeners.
4. **Stable CLI.** Keep the command names, arguments, and exit codes.
5. **Messages in English.** The project is public. Keep every user-facing message in English.

## Layout

```
src/ai_vlc/
  cli.py            commands, output formatting, exit codes
  _player.py        starts and stops VLC as a systemd user unit
  _rc_client.py     line-based RC protocol: connect, send, read until prompt
  _system_volume.py PipeWire volume through wpctl
tests/              pytest suite using a fake RC server; no VLC needed
```

## Setup and checks

```bash
python3 -m venv ~/.venvs/ai-vlc-dev
~/.venvs/ai-vlc-dev/bin/pip install -e ".[dev]"
~/.venvs/ai-vlc-dev/bin/python -m pytest -q
```

Tests must not start a real VLC or change the real system volume. Use the fakes in the test files.

## Rules for changes

- One change per pull request, with a test for each behaviour change.
- Keep the Python floor at 3.9. Do not add runtime dependencies.
- Do not change timeouts without a test that shows why.

## Security

Do not report vulnerabilities in a public issue. Use a private security advisory on GitHub.
