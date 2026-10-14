"""Control over the OS-level default audio output volume, via ``wpctl``.

This is a *separate* volume knob from VLC's own internal volume (see
``cli.cmd_volume``): VLC's volume scales the samples it produces, but that
signal still passes through the system's default output device at whatever
level PipeWire/WirePlumber has it set to. A quiet system sink can make
playback inaudible even while VLC itself reports 100% volume and "playing" --
this module exists because that's a real, previously-hit failure mode (see
DEVLOG.md 2026-07-18), not a hypothetical one.
"""

from __future__ import annotations

import re
import shutil
import subprocess

_DEFAULT_SINK = "@DEFAULT_AUDIO_SINK@"
_VOLUME_RE = re.compile(r"Volume:\s*([\d.]+)(\s*\[MUTED\])?")


class SystemVolumeError(RuntimeError):
    """Raised when wpctl is missing or a system volume operation fails."""


def _wpctl_path() -> str:
    found = shutil.which("wpctl")
    if not found:
        raise SystemVolumeError("wpctl not found. This machine may not use PipeWire for audio")
    return found


def get_system_volume() -> dict:
    """Return ``{"percent": int, "muted": bool}`` for the default sink."""
    result = subprocess.run(
        [_wpctl_path(), "get-volume", _DEFAULT_SINK],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemVolumeError(f"wpctl get-volume failed: {result.stderr.strip()}")

    match = _VOLUME_RE.search(result.stdout)
    if not match:
        raise SystemVolumeError(f"could not parse wpctl output: {result.stdout!r}")
    return {"percent": round(float(match.group(1)) * 100), "muted": bool(match.group(2))}


def set_system_volume(percent: int) -> dict:
    """Set the default sink's volume to *percent* (0-100), return new state."""
    wpctl = _wpctl_path()
    # Best-effort: clear [MUTED] so the new volume is actually audible.
    try:
        subprocess.run(
            [wpctl, "set-mute", _DEFAULT_SINK, "0"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        pass

    result = subprocess.run(
        [wpctl, "set-volume", _DEFAULT_SINK, f"{percent}%"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemVolumeError(f"wpctl set-volume failed: {result.stderr.strip()}")
    return get_system_volume()
