"""CLI entry point for ai-vlc."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_vlc._player import PlayerError, is_running, start, stop
from ai_vlc._rc_client import (
    RCError,
    RCTimeoutError,
    parse_status,
    run_command,
    run_commands,
    run_transition_command,
    set_volume,
)
from ai_vlc._system_volume import SystemVolumeError, get_system_volume, set_system_volume

_VERSION = "ai-vlc 0.1.0"


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _resolve_path(raw: str) -> str | None:
    """Return a playable path/URL, or None if a local path doesn't exist.

    URLs are passed through untouched; local paths are expanded (``~``) and
    resolved to absolute so systemd-run's minimal environment can find them.
    """
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    path = Path(raw).expanduser()
    if not path.exists():
        return None
    return str(path.resolve())


def _format_status(status_text: str, prefix: str = "") -> str:
    info = parse_status(status_text)
    title = Path(info["input"]).name if info["input"] else "unknown"
    state_display = info["state"] or "unknown"
    volume_pct = round(info["volume"] / 256 * 100) if info["volume"] is not None else "?"
    line = f"{prefix}{title} · {state_display} · volume {volume_pct}%"

    # VLC's own volume is a separate knob from the system output device's
    # volume (see _system_volume.py) -- playback can report fine here while
    # the actual sink is too quiet to hear. Surface both so that gap doesn't
    # need a separate manual check every time. Best-effort: a machine without
    # wpctl/PipeWire just doesn't get this line, status still works.
    try:
        sys_vol = get_system_volume()
    except SystemVolumeError:
        return line
    muted_display = " (muted)" if sys_vol["muted"] else ""
    return f"{line} · system volume {sys_vol['percent']}%{muted_display}"


def _print_status(prefix: str = "") -> int:
    """Query and print the current player status. Assumes the unit is running."""
    try:
        text = run_command("status")
    except RCError as exc:
        return _error(str(exc))
    print(_format_status(text, prefix))
    return 0


# Commands that trigger an actual track transition (new demuxer/decoder) need
# to poll status until it lands rather than trust an immediate read -- see
# run_transition_command()'s docstring. volume has the same issue and its own
# poll-based helper (set_volume, used directly by cmd_volume); pause is a
# plain synchronous state flip and is fine with a plain run_commands() pair.
_TRANSITION_COMMANDS = {"add", "next", "prev"}


def _run_and_print_status(command: str) -> int:
    """Run *command* then print the status once it reflects the change."""
    verb = command.split(" ", 1)[0]
    try:
        if verb in _TRANSITION_COMMANDS:
            status_text = run_transition_command(command)
        else:
            _, status_text = run_commands([command, "status"])
    except RCTimeoutError as exc:
        return _error(f"command sent but its effect was not confirmed: {exc}")
    except RCError as exc:
        return _error(str(exc))
    print(_format_status(status_text))
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    path = _resolve_path(args.path)
    if path is None:
        return _error(f"path not found: {args.path}")
    try:
        start(path, crop=args.crop)
    except PlayerError as exc:
        return _error(str(exc))
    print(f"now playing: {path}")
    return _print_status()


def cmd_add(args: argparse.Namespace) -> int:
    if not is_running():
        return cmd_play(args)
    path = _resolve_path(args.path)
    if path is None:
        return _error(f"path not found: {args.path}")
    print(f"added to the playlist: {path}")
    return _run_and_print_status(f"add {path}")


def cmd_pause(args: argparse.Namespace) -> int:
    if not is_running():
        return _error("nothing is playing")
    return _run_and_print_status("pause")


def cmd_stop(args: argparse.Namespace) -> int:
    if not is_running():
        print("nothing is playing.")
        return 0
    try:
        stop()
    except PlayerError as exc:
        return _error(str(exc))
    print("stopped.")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    if not is_running():
        return _error("nothing is playing")
    return _run_and_print_status("next")


def cmd_prev(args: argparse.Namespace) -> int:
    if not is_running():
        return _error("nothing is playing")
    return _run_and_print_status("prev")


def cmd_volume(args: argparse.Namespace) -> int:
    if not (0 <= args.percent <= 100):
        return _error("volume must be between 0 and 100")
    if not is_running():
        return _error("nothing is playing")
    vlc_volume = round(args.percent / 100 * 256)
    try:
        status_text = set_volume(vlc_volume)
    except RCTimeoutError as exc:
        return _error(f"command sent but its effect was not confirmed: {exc}")
    except RCError as exc:
        return _error(str(exc))
    print(_format_status(status_text))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if not is_running():
        print("nothing is playing.")
        return 0
    return _print_status()


def cmd_system_volume(args: argparse.Namespace) -> int:
    """Get or set the OS-level default output device's volume.

    This is deliberately independent of ``ai-vlc volume`` (VLC's own
    volume) -- the two multiply together for actual loudness, and a quiet
    system output can make playback inaudible even at VLC's 100%.
    """
    try:
        if args.percent is None:
            info = get_system_volume()
        else:
            if not (0 <= args.percent <= 100):
                return _error("volume must be between 0 and 100")
            info = set_system_volume(args.percent)
    except SystemVolumeError as exc:
        return _error(str(exc))

    muted_display = " (muted)" if info["muted"] else ""
    print(f"system output volume: {info['percent']}%{muted_display}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-vlc",
        description="Background music playback control, backed by VLC.",
    )
    parser.add_argument("--version", action="version", version=_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    p_play = sub.add_parser(
        "play",
        help="Play a file, directory, or URL (replaces current playback). "
        "Shows a video window automatically for actual video content, no flag needed",
    )
    p_play.add_argument("path", help="Local file/directory path or a streamable URL")
    p_play.add_argument(
        "--crop",
        default=None,
        help="Crop ratio to remove letterbox bars, e.g. 2.35:1 or 2.39:1 (VLC can't auto-detect these, you pick)",
    )

    p_add = sub.add_parser("add", help="Add a file/directory/URL to the playlist and play it")
    p_add.add_argument("path", help="Local file/directory path or a streamable URL")
    p_add.add_argument(
        "--crop",
        default=None,
        help="Same as `play --crop`; only takes effect if nothing is currently playing "
        "(falls back to play), since crop is a launch-time VLC option",
    )

    sub.add_parser("pause", help="Toggle play/pause")
    sub.add_parser("stop", help="Stop playback and release the player")
    sub.add_parser("next", help="Skip to the next playlist item")
    sub.add_parser("prev", help="Go back to the previous playlist item")

    p_volume = sub.add_parser("volume", help="Set VLC's own volume (0-100)")
    p_volume.add_argument("percent", type=int, help="Volume percentage, 0-100")

    p_sysvolume = sub.add_parser(
        "system-volume",
        help="Get/set the OS-level default output device's volume (independent of VLC's own volume)",
    )
    p_sysvolume.add_argument(
        "percent", type=int, nargs="?", default=None, help="Volume percentage, 0-100; omit to just show it"
    )

    sub.add_parser("status", help="Show what's currently playing")

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if argv is None:
        argv = sys.argv[1:]

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    handlers = {
        "play": cmd_play,
        "add": cmd_add,
        "pause": cmd_pause,
        "stop": cmd_stop,
        "next": cmd_next,
        "prev": cmd_prev,
        "volume": cmd_volume,
        "system-volume": cmd_system_volume,
        "status": cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
