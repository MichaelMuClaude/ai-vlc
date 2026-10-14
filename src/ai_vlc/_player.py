"""Lifecycle management for the background VLC process.

VLC runs as a transient systemd --user unit so "is it running" / "stop it" are just systemctl calls
instead of a hand-rolled PID file that can go stale. Playback control itself
(pause/next/volume/...) goes over the RC socket, see ``_rc_client.py``.
"""

from __future__ import annotations

import shutil
import subprocess

from ai_vlc._rc_client import RC_PORT, RCError, connect

UNIT_NAME = "ai-vlc-player"


class PlayerError(RuntimeError):
    """Raised when starting/stopping the VLC unit fails."""


def is_running() -> bool:
    """Return True if the ai-vlc-player systemd unit is active."""
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", f"{UNIT_NAME}.service"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _vlc_path() -> str:
    found = shutil.which("vlc")
    if not found:
        raise PlayerError("vlc command not found. Check that VLC is installed (sudo apt install vlc)")
    return found


def start(path: str, *, port: int = RC_PORT, crop: str | None = None) -> None:
    """Stop any existing player, then start a fresh VLC instance on *path*.

    *path* can be a local file, a directory (played as a playlist, recursing
    into subdirectories), or a URL VLC can stream. Raises ``PlayerError`` if
    systemd-run fails to launch the unit, or if the RC socket never comes up
    (e.g. VLC exited immediately because the media was invalid).

    *crop* is an optional VLC ``--crop`` ratio (e.g. ``"2.35:1"``) applied at
    launch, for widescreen movies that are letterboxed into a 16:9 frame --
    VLC has no auto-detect for the bars, this just skips the manual
    Video > Crop menu click every time.

    Launches the *real* VLC (with its normal Qt GUI, not ``cvlc``'s headless
    ``dummy`` interface) and adds the RC socket as an *extra* interface
    (``--extraintf``, not ``--intf``) alongside it. This gives two
    simultaneous, independent control paths to the same player: the user can
    click the actual on-screen VLC window (play/pause/seek/volume), and
    ``ai-vlc``'s CLI still drives it over the RC socket exactly as before
    -- confirmed empirically that both work at once on the same process.

    Always passes ``--vout x11`` rather than ``--no-video`` -- confirmed
    empirically that VLC simply doesn't open a video surface for audio-only
    media regardless of the vout module, so this is safe as the unconditional
    default: movies get a real window with no flag needed, and pure audio
    playback still shows VLC's normal (audio-only) player window. ``x11``
    specifically (not VLC's auto-detected default) because on this machine's
    hybrid AMD+NVIDIA GPU setup, auto-detect tries VAAPI-accelerated output
    first, that fails ("video output creation failed" in the logs, confirmed
    empirically) and no window ever appears for real video -- plain X11
    output reliably does.
    """
    stop()

    cmd = [
        "systemd-run",
        "--user",
        f"--unit={UNIT_NAME}",
        "--collect",
        _vlc_path(),
        "--extraintf",
        "rc",
        "--rc-host",
        f"127.0.0.1:{port}",
        "--recursive=expand",
        "--random",
        "--vout",
        "x11",
    ]
    if crop:
        cmd += ["--crop", crop]
    cmd.append(path)

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise PlayerError(f"systemd-run failed to start VLC (exit {result.returncode}): {result.stderr.strip()}")

    try:
        sock = connect(port=port, connect_timeout=5.0)
        sock.close()
    except RCError as exc:
        stop()
        raise PlayerError(f"VLC started but its RC interface did not come up; the media path may be wrong: {exc}") from exc


def stop() -> None:
    """Stop the VLC unit if it's running. Tolerates it already being gone."""
    result = subprocess.run(
        ["systemctl", "--user", "stop", f"{UNIT_NAME}.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    stderr = result.stderr.lower()
    if any(phrase in stderr for phrase in ("not loaded", "does not exist", "invalid unit name")):
        return
    raise PlayerError(f"failed to stop VLC (exit {result.returncode}): {result.stderr.strip()}")
