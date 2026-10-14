# Contributing to ai-vlc

Humans and AI agents are both welcome. The detailed rules for agents are in [AGENTS.md](AGENTS.md).

- Commands must confirm their effect, or report that they could not.
- Keep messages in English.
- Add a test for each change. The tests use a fake RC server, so they do not need VLC.

```bash
python3 -m venv ~/.venvs/ai-vlc-dev
~/.venvs/ai-vlc-dev/bin/pip install -e ".[dev]"
~/.venvs/ai-vlc-dev/bin/python -m pytest -q
```

Describe what changed, why, and which tests you ran. If an AI agent helped, say so and name the tool and model.

By contributing, you agree that your contribution is released under the MIT licence in [LICENSE](LICENSE).
