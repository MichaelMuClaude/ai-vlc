"""Tests for ai_vlc.cli, with _player/_rc_client mocked out.

None of these tests touch a real VLC process or systemd unit -- cli.py's
dependencies (is_running/start/stop/run_command) are patched directly, the
a fake systemd layer, the same approach as the RC tests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_vlc import cli
from ai_vlc._player import PlayerError
from ai_vlc._rc_client import RCError, RCTimeoutError
from ai_vlc._system_volume import SystemVolumeError


STATUS_PLAYING = "( new input: file:///a/song.mp3 )\r\n( audio volume: 256 )\r\n( state playing )"
STATUS_PAUSED = "( new input: file:///a/song.mp3 )\r\n( audio volume: 128 )\r\n( state paused )"


@pytest.fixture(autouse=True)
def _no_real_system_volume():
    """Keep tests hermetic: never let _format_status hit the real wpctl/audio
    system. Individual tests that care about the system-volume-in-status
    integration override this with their own patch.object(cli, "get_system_volume", ...).
    """
    with patch.object(cli, "get_system_volume", side_effect=SystemVolumeError("no wpctl in test env")):
        yield


class TestPlay:
    def test_play_missing_path_fails(self, capsys):
        rc = cli.main(["play", "/no/such/file.mp3"])
        assert rc != 0
        assert "path not found" in capsys.readouterr().err

    def test_play_valid_path_starts_player(self, tmp_path, capsys):
        song = tmp_path / "song.mp3"
        song.write_bytes(b"fake")
        with patch.object(cli, "start") as mock_start, patch.object(
            cli, "run_command", return_value=STATUS_PLAYING
        ):
            rc = cli.main(["play", str(song)])
        assert rc == 0
        mock_start.assert_called_once_with(str(song), crop=None)
        out = capsys.readouterr().out
        assert "song.mp3" in out
        assert "playing" in out

    def test_play_url_passthrough(self, capsys):
        with patch.object(cli, "start") as mock_start, patch.object(
            cli, "run_command", return_value=STATUS_PLAYING
        ):
            rc = cli.main(["play", "https://example.com/stream.mp3"])
        assert rc == 0
        mock_start.assert_called_once_with("https://example.com/stream.mp3", crop=None)

    def test_play_with_crop_passes_through(self, tmp_path, capsys):
        song = tmp_path / "movie.mp4"
        song.write_bytes(b"fake")
        with patch.object(cli, "start") as mock_start, patch.object(
            cli, "run_command", return_value=STATUS_PLAYING
        ):
            rc = cli.main(["play", str(song), "--crop", "2.35:1"])
        assert rc == 0
        mock_start.assert_called_once_with(str(song), crop="2.35:1")

    def test_play_player_error_surfaces(self, tmp_path, capsys):
        song = tmp_path / "song.mp3"
        song.write_bytes(b"fake")
        with patch.object(cli, "start", side_effect=PlayerError("boom")):
            rc = cli.main(["play", str(song)])
        assert rc != 0
        assert "boom" in capsys.readouterr().err


class TestAdd:
    def test_add_when_nothing_running_behaves_like_play(self, tmp_path, capsys):
        song = tmp_path / "song.mp3"
        song.write_bytes(b"fake")
        with patch.object(cli, "is_running", return_value=False), patch.object(
            cli, "start"
        ) as mock_start, patch.object(cli, "run_command", return_value=STATUS_PLAYING):
            rc = cli.main(["add", str(song)])
        assert rc == 0
        mock_start.assert_called_once_with(str(song), crop=None)

    def test_add_when_running_appends_via_rc(self, tmp_path, capsys):
        song = tmp_path / "song.mp3"
        song.write_bytes(b"fake")
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_transition_command", return_value=STATUS_PLAYING
        ) as mock_run:
            rc = cli.main(["add", str(song)])
        assert rc == 0
        mock_run.assert_called_once_with(f"add {song}")

    def test_add_missing_path_fails(self):
        with patch.object(cli, "is_running", return_value=True):
            rc = cli.main(["add", "/no/such/file.mp3"])
        assert rc != 0


class TestPauseNextPrev:
    def test_pause_when_not_running_fails(self, capsys):
        with patch.object(cli, "is_running", return_value=False):
            rc = cli.main(["pause"])
        assert rc != 0
        assert "nothing is playing" in capsys.readouterr().err

    def test_pause_sends_rc_command(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_commands", return_value=["", STATUS_PAUSED]
        ) as mock_run:
            rc = cli.main(["pause"])
        assert rc == 0
        mock_run.assert_called_once_with(["pause", "status"])
        assert "paused" in capsys.readouterr().out

    def test_next_sends_rc_command(self):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_transition_command", return_value=STATUS_PLAYING
        ) as mock_run:
            rc = cli.main(["next"])
        assert rc == 0
        mock_run.assert_called_once_with("next")

    def test_prev_sends_rc_command(self):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_transition_command", return_value=STATUS_PLAYING
        ) as mock_run:
            rc = cli.main(["prev"])
        assert rc == 0
        mock_run.assert_called_once_with("prev")

    def test_next_when_not_running_fails(self):
        with patch.object(cli, "is_running", return_value=False):
            assert cli.main(["next"]) != 0

    def test_rc_error_surfaces(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_commands", side_effect=RCError("socket dead")
        ):
            rc = cli.main(["pause"])
        assert rc != 0
        assert "socket dead" in capsys.readouterr().err

    def test_rc_timeout_error_returns_nonzero(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_transition_command", side_effect=RCTimeoutError("timeout")
        ):
            rc = cli.main(["next"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "not confirmed" in err


class TestStop:
    def test_stop_when_not_running(self, capsys):
        with patch.object(cli, "is_running", return_value=False):
            rc = cli.main(["stop"])
        assert rc == 0
        assert "nothing is playing" in capsys.readouterr().out

    def test_stop_when_running(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(cli, "stop") as mock_stop:
            rc = cli.main(["stop"])
        assert rc == 0
        mock_stop.assert_called_once()
        assert "stopped" in capsys.readouterr().out


class TestVolume:
    def test_volume_out_of_range_rejected(self, capsys):
        rc = cli.main(["volume", "150"])
        assert rc != 0
        assert "between 0 and 100" in capsys.readouterr().err

    def test_volume_negative_rejected(self):
        assert cli.main(["volume", "-5"]) != 0

    def test_volume_when_not_running_fails(self):
        with patch.object(cli, "is_running", return_value=False):
            assert cli.main(["volume", "50"]) != 0

    def test_volume_converts_percent_to_vlc_scale(self):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "set_volume", return_value=STATUS_PLAYING
        ) as mock_set:
            rc = cli.main(["volume", "50"])
        assert rc == 0
        mock_set.assert_called_once_with(128)

    def test_volume_100_maps_to_256(self):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "set_volume", return_value=STATUS_PLAYING
        ) as mock_set:
            cli.main(["volume", "100"])
        mock_set.assert_called_once_with(256)

    def test_volume_rc_error_surfaces(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "set_volume", side_effect=RCError("socket dead")
        ):
            rc = cli.main(["volume", "50"])
        assert rc != 0
        assert "socket dead" in capsys.readouterr().err

    def test_volume_rc_timeout_error_returns_nonzero(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "set_volume", side_effect=RCTimeoutError("timeout")
        ):
            rc = cli.main(["volume", "50"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "not confirmed" in err


class TestStatus:
    def test_status_when_not_running(self, capsys):
        with patch.object(cli, "is_running", return_value=False):
            rc = cli.main(["status"])
        assert rc == 0
        assert "nothing is playing" in capsys.readouterr().out

    def test_status_when_running_shows_title_and_volume(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_command", return_value=STATUS_PLAYING
        ):
            rc = cli.main(["status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "song.mp3" in out
        assert "100%" in out

    def test_status_includes_system_volume_when_available(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_command", return_value=STATUS_PLAYING
        ), patch.object(cli, "get_system_volume", return_value={"percent": 17, "muted": False}):
            rc = cli.main(["status"])
        assert rc == 0
        assert "system volume 17%" in capsys.readouterr().out

    def test_status_flags_system_mute(self, capsys):
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_command", return_value=STATUS_PLAYING
        ), patch.object(cli, "get_system_volume", return_value={"percent": 50, "muted": True}):
            rc = cli.main(["status"])
        assert "muted" in capsys.readouterr().out

    def test_status_omits_system_volume_when_unavailable(self, capsys):
        # The autouse fixture already makes get_system_volume raise; just
        # confirm status still succeeds without that segment.
        with patch.object(cli, "is_running", return_value=True), patch.object(
            cli, "run_command", return_value=STATUS_PLAYING
        ):
            rc = cli.main(["status"])
        assert rc == 0
        assert "system volume" not in capsys.readouterr().out


class TestSystemVolume:
    def test_no_args_shows_current(self, capsys):
        with patch.object(cli, "get_system_volume", return_value={"percent": 42, "muted": False}):
            rc = cli.main(["system-volume"])
        assert rc == 0
        assert "42%" in capsys.readouterr().out

    def test_with_arg_sets_it(self, capsys):
        with patch.object(cli, "set_system_volume", return_value={"percent": 70, "muted": False}) as mock_set:
            rc = cli.main(["system-volume", "70"])
        assert rc == 0
        mock_set.assert_called_once_with(70)
        assert "70%" in capsys.readouterr().out

    def test_out_of_range_rejected(self, capsys):
        rc = cli.main(["system-volume", "150"])
        assert rc != 0
        assert "between 0 and 100" in capsys.readouterr().err

    def test_negative_rejected(self):
        assert cli.main(["system-volume", "-5"]) != 0

    def test_muted_shown(self, capsys):
        with patch.object(cli, "get_system_volume", return_value={"percent": 17, "muted": True}):
            rc = cli.main(["system-volume"])
        assert "muted" in capsys.readouterr().out

    def test_error_surfaces(self, capsys):
        with patch.object(cli, "get_system_volume", side_effect=SystemVolumeError("no wpctl")):
            rc = cli.main(["system-volume"])
        assert rc != 0
        assert "no wpctl" in capsys.readouterr().err


def test_version(capsys):
    rc = cli.main(["--version"])
    assert rc == 0
    assert "ai-vlc" in capsys.readouterr().out
