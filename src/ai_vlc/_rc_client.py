"""Client for VLC's line-based "RC" (remote control) TCP interface.

VLC is started with ``--intf rc --rc-host 127.0.0.1:<port>``, which opens a
plain-text socket: every command is a line of text, every response ends with
a ``"> "`` prompt. This module knows only that wire protocol; it has no idea
how VLC was launched (see ``_player.py`` for that).
"""

from __future__ import annotations

import socket
import time

RC_PORT = 42130
_PROMPT = b"> "
_CONNECT_RETRY_INTERVAL = 0.2


class RCError(RuntimeError):
    """Raised when the RC socket can't be reached or a command fails."""


class RCTimeoutError(RCError):
    """Raised when a command was sent but its effect was not confirmed in time."""


def _read_until_prompt(sock: socket.socket, overall_timeout: float) -> str:
    """Read from *sock* until the trailing ``"> "`` prompt appears."""
    sock.settimeout(overall_timeout)
    buf = b""
    deadline = time.monotonic() + overall_timeout
    while not buf.endswith(_PROMPT):
        if time.monotonic() > deadline:
            raise RCError("VLC did not respond in time (RC socket read timed out)")
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, OSError) as exc:
            raise RCError("VLC did not respond in time (RC socket read timed out)") from exc
        if not chunk:
            raise RCError("VLC closed the RC connection (the process may have exited)")
        buf += chunk
    return buf.decode("utf-8", errors="replace")


def connect(port: int = RC_PORT, *, connect_timeout: float = 3.0) -> socket.socket:
    """Connect to VLC's RC socket, retrying until *connect_timeout* elapses.

    VLC needs a brief moment after the process starts before the RC listener
    is ready, so a single immediate connect attempt is not reliable.
    """
    deadline = time.monotonic() + connect_timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            _read_until_prompt(sock, overall_timeout=2.0)  # consume the banner
            return sock
        except (OSError, RCError) as exc:
            last_error = exc
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            time.sleep(_CONNECT_RETRY_INTERVAL)
    raise RCError(f"could not connect to VLC's RC port 127.0.0.1:{port}: {last_error}")


def send_command(sock: socket.socket, command: str, *, timeout: float = 2.0) -> str:
    """Send *command* and return its response text (prompt stripped)."""
    try:
        sock.sendall((command + "\n").encode("utf-8"))
    except OSError as exc:
        raise RCError(f"failed to send RC command (connection lost): {exc}") from exc
    raw = _read_until_prompt(sock, overall_timeout=timeout)
    # Response is "<output>\r\n> " -- strip the trailing prompt.
    if raw.endswith(_PROMPT.decode()):
        raw = raw[: -len(_PROMPT)]
    return raw.strip("\r\n")


def run_command(command: str, port: int = RC_PORT) -> str:
    """Open a short-lived connection, run one command, return its response."""
    return run_commands([command], port=port)[0]


def run_commands(commands: list[str], port: int = RC_PORT) -> list[str]:
    """Run multiple commands over a single connection, in order.

    Using one connection (rather than a fresh ``run_command()`` per command)
    guarantees the commands are seen by VLC's RC interface strictly
    sequentially, and each ``send_command`` call blocks until VLC's reply
    prompt for that exact command comes back before the next one is sent --
    enough to make simple state mutations (e.g. ``volume 128``) visible to an
    immediately following ``status``. NOT enough for commands that trigger an
    actual track transition (``add``/``next``/``prev``) -- see
    ``run_transition_command`` for those.
    """
    sock = connect(port=port)
    try:
        return [send_command(sock, command) for command in commands]
    finally:
        sock.close()


def _run_then_poll_until(
    sock: socket.socket,
    command: str,
    is_landed,
    *,
    poll_interval: float,
    max_wait: float,
) -> str:
    """Send *command*, then poll ``status`` until ``is_landed(parsed)`` is
    true or *max_wait* elapses.

    Returns the status text once the predicate is satisfied. If *max_wait*
    elapses first, raises :class:`RCTimeoutError` so callers can distinguish
    "command sent but effect not confirmed" from a successful landing.

    Shared by ``run_transition_command`` and ``set_volume`` -- both need to
    work around the same VLC quirk: the RC reply prompt for a command that
    triggers real internal work (track switch, audio output re-init) comes
    back as soon as the request is *queued*, not once it's actually applied.
    """
    send_command(sock, command)
    deadline = time.monotonic() + max_wait
    latest_text = send_command(sock, "status")
    while True:
        if is_landed(parse_status(latest_text)):
            return latest_text
        if time.monotonic() >= deadline:
            raise RCTimeoutError(
                f"command sent but its effect was not confirmed (last state: {latest_text.strip()!r})"
            )
        time.sleep(poll_interval)
        latest_text = send_command(sock, "status")


def run_transition_command(
    command: str,
    port: int = RC_PORT,
    *,
    poll_interval: float = 0.1,
    max_wait: float = 2.0,
) -> str:
    """Run a track-changing command (``add``/``next``/``prev``) and return
    ``status`` text that actually reflects the new track.

    VLC's RC handler for these returns its "> " prompt as soon as the
    request is *queued* -- the demuxer/decoder switch to the new input
    happens on a separate thread a beat later, so a ``status`` sent
    immediately after can still report the pre-transition title (confirmed
    empirically by polling ``status`` in a tight loop right after ``add``).

    Rather than guess a fixed delay, this takes a ``status`` snapshot
    *before* sending *command*, then polls ``status`` afterward until the
    reported input differs from that snapshot (transition landed). If
    *max_wait* elapses first, :class:`RCTimeoutError` is raised.
    """
    sock = connect(port=port)
    try:
        before = parse_status(send_command(sock, "status"))
        return _run_then_poll_until(
            sock,
            command,
            lambda latest: latest["input"] != before["input"] or latest["state"] != before["state"],
            poll_interval=poll_interval,
            max_wait=max_wait,
        )
    finally:
        sock.close()


def set_volume(
    vlc_volume: int,
    port: int = RC_PORT,
    *,
    poll_interval: float = 0.05,
    max_wait: float = 1.0,
) -> str:
    """Set the volume (VLC's 0-256 scale) and return ``status`` text that
    actually reflects it.

    Same underlying issue as ``run_transition_command``: setting the volume
    right after a track transition can race the transition's audio-output
    re-init and silently not stick. Confirmed empirically: a `volume`
    command issued immediately after `next`/`prev`/`add` can report the OLD
    volume on the very next `status`, catching up only one command later.
    Polling here closes that gap the same way.
    """
    sock = connect(port=port)
    try:
        return _run_then_poll_until(
            sock,
            f"volume {vlc_volume}",
            lambda latest: latest["volume"] == vlc_volume,
            poll_interval=poll_interval,
            max_wait=max_wait,
        )
    finally:
        sock.close()


def parse_status(status_text: str) -> dict:
    """Parse the multi-line output of VLC's ``status`` RC command.

    Example input::

        ( new input: file:///path/to/song.mp3 )
        ( audio volume: 128 )
        ( state playing )

    Returns a dict with keys ``state`` ("playing"/"paused"/"stopped"/None),
    ``volume`` (int or None) and ``input`` (str or None, the raw MRL).
    """
    result: dict = {"state": None, "volume": None, "input": None}
    for line in status_text.splitlines():
        line = line.strip().strip("()").strip()
        if line.startswith("state "):
            result["state"] = line.removeprefix("state ").strip()
        elif line.startswith("audio volume:"):
            try:
                result["volume"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("new input:"):
            result["input"] = line.split(":", 1)[1].strip()
    return result
