"""Tests for ai_vlc._rc_client.

Uses a tiny fake TCP server that mimics VLC's RC protocol (banner, then
echo-with-prompt for each line) instead of depending on a real VLC process.
"""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from ai_vlc._rc_client import (
    RCError,
    RCTimeoutError,
    connect,
    parse_status,
    run_command,
    run_commands,
    run_transition_command,
    send_command,
    set_volume,
)


class _FakeRCServer:
    """A minimal stand-in for VLC's RC socket.

    Responses are looked up from a dict of {command: response_body}; unknown
    commands get an empty body. Every response is followed by the "> " prompt,
    matching VLC's real wire format.
    """

    def __init__(self, responses: dict[str, str] | None = None, banner: bytes = b"banner\r\n> "):
        self.responses = responses or {}
        self.banner = banner
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            conn.sendall(self.banner)
            buf = b""
            conn.settimeout(2.0)
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    cmd = line.decode().strip()
                    body = self.responses.get(cmd, "")
                    reply = (body + "\r\n> ") if body else "> "
                    conn.sendall(reply.encode())

    def close(self):
        self._sock.close()


@pytest.fixture
def fake_server():
    server = _FakeRCServer(
        responses={
            "get_title": "song.mp3",
            "status": "( new input: file:///a/song.mp3 )\r\n( audio volume: 128 )\r\n( state playing )",
        }
    )
    yield server
    server.close()


def test_connect_consumes_banner_and_returns_socket(fake_server):
    sock = connect(port=fake_server.port, connect_timeout=2.0)
    try:
        # If the banner wasn't consumed, this would see banner text leak in.
        reply = send_command(sock, "get_title")
        assert reply == "song.mp3"
    finally:
        sock.close()


def test_send_command_strips_prompt(fake_server):
    sock = connect(port=fake_server.port, connect_timeout=2.0)
    try:
        reply = send_command(sock, "status")
        assert "state playing" in reply
        assert not reply.endswith("> ")
    finally:
        sock.close()


def test_run_command_opens_and_closes_connection(fake_server):
    reply = run_command("get_title", port=fake_server.port)
    assert reply == "song.mp3"


def test_run_commands_runs_all_in_order(fake_server):
    replies = run_commands(["get_title", "status"], port=fake_server.port)
    assert replies == ["song.mp3", "( new input: file:///a/song.mp3 )\r\n( audio volume: 128 )\r\n( state playing )"]


class _DelayedTransitionServer:
    """Fake RC server whose ``status`` reply changes after N polls.

    Models VLC's real behaviour after add/next/prev: the command's own reply
    is an immediate empty ack, but ``status`` keeps reporting the *old*
    input for a few polls before finally reflecting the transition.
    """

    STATUS_BEFORE = "( new input: file:///a/song.mp3 )\r\n( audio volume: 256 )\r\n( state playing )"
    STATUS_AFTER = "( new input: file:///b/other.mp3 )\r\n( audio volume: 256 )\r\n( state playing )"

    def __init__(self, lands_after_polls: int, status_before: str | None = None, status_after: str | None = None):
        self.lands_after_polls = lands_after_polls
        if status_before is not None:
            self.STATUS_BEFORE = status_before
        if status_after is not None:
            self.STATUS_AFTER = status_after
        self._status_calls = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            conn.sendall(b"banner\r\n> ")
            buf = b""
            conn.settimeout(3.0)
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    cmd = line.decode().strip()
                    if cmd == "status":
                        self._status_calls += 1
                        body = (
                            self.STATUS_AFTER
                            if self._status_calls > self.lands_after_polls
                            else self.STATUS_BEFORE
                        )
                        conn.sendall((body + "\r\n> ").encode())
                    else:
                        conn.sendall(b"> ")

    def close(self):
        self._sock.close()


class TestRunTransitionCommand:
    def test_returns_immediately_when_already_landed(self):
        # lands_after_polls=1 means the first status *after* the transition
        # command already reflects the new track, so no further polling is
        # needed.
        server = _DelayedTransitionServer(lands_after_polls=1)
        try:
            text = run_transition_command("next", port=server.port, poll_interval=0.01, max_wait=1.0)
            assert "other.mp3" in text
        finally:
            server.close()

    def test_polls_until_transition_lands(self):
        server = _DelayedTransitionServer(lands_after_polls=3)
        try:
            text = run_transition_command("next", port=server.port, poll_interval=0.02, max_wait=2.0)
            assert "other.mp3" in text
        finally:
            server.close()

    def test_gives_up_after_max_wait_and_raises(self):
        # Transition that "never" lands within the window: max_wait expires
        # first, so we raise RCTimeoutError instead of silently returning a
        # stale status.
        server = _DelayedTransitionServer(lands_after_polls=1000)
        try:
            with pytest.raises(RCTimeoutError):
                run_transition_command("next", port=server.port, poll_interval=0.02, max_wait=0.2)
        finally:
            server.close()


class TestSetVolume:
    _BEFORE = "( new input: file:///a/song.mp3 )\r\n( audio volume: 256 )\r\n( state playing )"
    _AFTER = "( new input: file:///a/song.mp3 )\r\n( audio volume: 128 )\r\n( state playing )"

    def test_returns_immediately_when_already_landed(self):
        server = _DelayedTransitionServer(0, status_before=self._AFTER, status_after=self._AFTER)
        try:
            text = set_volume(128, port=server.port, poll_interval=0.01, max_wait=1.0)
            assert "audio volume: 128" in text
        finally:
            server.close()

    def test_polls_until_volume_change_lands(self):
        server = _DelayedTransitionServer(3, status_before=self._BEFORE, status_after=self._AFTER)
        try:
            text = set_volume(128, port=server.port, poll_interval=0.02, max_wait=2.0)
            assert "audio volume: 128" in text
        finally:
            server.close()

    def test_gives_up_after_max_wait_and_raises(self):
        server = _DelayedTransitionServer(1000, status_before=self._BEFORE, status_after=self._AFTER)
        try:
            with pytest.raises(RCTimeoutError):
                set_volume(128, port=server.port, poll_interval=0.02, max_wait=0.2)
        finally:
            server.close()


def test_connect_retries_until_server_ready():
    """connect() must poll rather than fail on the first attempt.

    Reserve a port, close it (so the first connect attempts genuinely fail
    with "connection refused"), then start the fake server on that same
    port shortly after -- connect() should still succeed by retrying.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    port = probe.getsockname()[1]
    probe.close()

    holder = {}

    def start_late():
        time.sleep(0.3)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(1)
        holder["server"] = server
        conn, _ = server.accept()
        conn.sendall(b"banner\r\n> ")
        holder["conn"] = conn

    thread = threading.Thread(target=start_late, daemon=True)
    thread.start()

    sock = connect(port=port, connect_timeout=3.0)
    sock.close()
    thread.join(timeout=1.0)
    holder["conn"].close()
    holder["server"].close()


def test_connect_times_out_if_nothing_listening():
    # Reserve then immediately free a port so nothing is listening on it.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    with pytest.raises(RCError):
        connect(port=port, connect_timeout=0.5)


class TestNetworkErrors:
    """Bug 1: OSError subclasses during send/recv must be wrapped as RCError."""

    def test_send_command_catches_broken_pipe_on_send(self):
        mock_sock = MagicMock()
        mock_sock.sendall.side_effect = BrokenPipeError(32, "Broken pipe")
        with pytest.raises(RCError, match="failed to send RC command"):
            send_command(mock_sock, "pause")

    def test_send_command_catches_connection_reset_on_send(self):
        mock_sock = MagicMock()
        mock_sock.sendall.side_effect = ConnectionResetError(104, "Connection reset by peer")
        with pytest.raises(RCError, match="failed to send RC command"):
            send_command(mock_sock, "pause")

    def test_send_command_catches_connection_reset_on_recv(self):
        mock_sock = MagicMock()
        mock_sock.sendall.return_value = None
        mock_sock.recv.side_effect = ConnectionResetError(104, "Connection reset by peer")
        with pytest.raises(RCError, match="timed out"):
            send_command(mock_sock, "pause")


class TestConnectSocketCleanup:
    """Bug 2: connect() must close the socket on handshake failure."""

    def test_closes_socket_when_banner_read_fails(self):
        mock_sock = MagicMock()
        with patch("socket.create_connection", return_value=mock_sock), patch(
            "ai_vlc._rc_client._read_until_prompt", side_effect=RCError("no banner")
        ):
            with pytest.raises(RCError):
                connect(port=12345, connect_timeout=0.3)
        assert mock_sock.close.called


class TestTimeoutRaises:
    """Bug 3: polling timeout must raise RCTimeoutError, not return stale status."""

    def test_run_transition_command_raises_when_max_wait_exceeded(self):
        server = _DelayedTransitionServer(lands_after_polls=1000)
        try:
            with pytest.raises(RCTimeoutError):
                run_transition_command("next", port=server.port, poll_interval=0.02, max_wait=0.1)
        finally:
            server.close()

    def test_set_volume_raises_when_max_wait_exceeded(self):
        before = "( new input: file:///a/song.mp3 )\r\n( audio volume: 256 )\r\n( state playing )"
        after = "( new input: file:///a/song.mp3 )\r\n( audio volume: 128 )\r\n( state playing )"
        server = _DelayedTransitionServer(1000, status_before=before, status_after=after)
        try:
            with pytest.raises(RCTimeoutError):
                set_volume(128, port=server.port, poll_interval=0.02, max_wait=0.1)
        finally:
            server.close()


class TestParseStatus:
    def test_parses_playing_state(self):
        text = "( new input: file:///a/song.mp3 )\r\n( audio volume: 128 )\r\n( state playing )"
        info = parse_status(text)
        assert info == {"state": "playing", "volume": 128, "input": "file:///a/song.mp3"}

    def test_parses_paused_state_without_new_input_line(self):
        text = "( audio volume: 256 )\r\n( state paused )"
        info = parse_status(text)
        assert info["state"] == "paused"
        assert info["volume"] == 256
        assert info["input"] is None

    def test_handles_empty_text(self):
        info = parse_status("")
        assert info == {"state": None, "volume": None, "input": None}
