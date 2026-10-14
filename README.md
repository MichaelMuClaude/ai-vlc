# ai-vlc

Control VLC playback from the command line, so that an AI agent can play, pause, skip, and adjust music or video with simple commands. VLC runs in the background as a systemd user service, so playback continues after the command returns.

```bash
ai-vlc play ~/Music/song.mp3          # play a file, folder, or stream URL
ai-vlc add ~/Music/next.mp3           # add to the playlist
ai-vlc pause                          # toggle play and pause
ai-vlc next
ai-vlc volume 60                      # VLC's own volume, 0-100
ai-vlc system-volume 40               # the system output volume, through PipeWire
ai-vlc status                         # what is playing and at what volume
ai-vlc stop
```

Playback commands check VLC's reply. If VLC does not confirm a change within the timeout, the command reports that the effect is unconfirmed instead of assuming it worked.

## Requirements

- Linux with **X11** (video uses `--vout x11`; Wayland-only sessions are not supported yet)
- **VLC** (`sudo apt install vlc`)
- **systemd** user services (`systemd-run --user`)
- **PipeWire** and `wpctl`, only for `system-volume` and the system-volume line in `status`. Without them, everything else still works.
- Python 3.9 or newer. There are no Python dependencies.

## How it works

- `play` or `add` starts VLC as a transient `systemd --user` unit with its remote-control (RC) interface on `127.0.0.1:42130`.
- Other commands connect to that port, send a line of text, and read VLC's reply.
- `stop` stops the unit.

Video is detected from the file type, and the player picks the right output automatically.

## Install

```bash
pip install -e .
```

## Development

```bash
python3 -m venv ~/.venvs/ai-vlc-dev
~/.venvs/ai-vlc-dev/bin/pip install -e ".[dev]"
~/.venvs/ai-vlc-dev/bin/python -m pytest -q
```

The tests use a fake RC server, so they do not need VLC or a display.

## Limits

- Only one player at a time. A new `play` replaces the current one.
- The RC port is fixed at 127.0.0.1:42130.
- Status shows what VLC reports. It doesn't read metadata such as album or artist.

## Licence

MIT. See [LICENSE](LICENSE). VLC itself is GPL-2.0 and is a separate program that this tool controls.
