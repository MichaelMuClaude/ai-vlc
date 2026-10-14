"""Tests for ai_vlc._player, with subprocess/RC connect mocked out."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from ai_vlc import _player
from ai_vlc._player import PlayerError, start, stop


def _ok_result():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _fail_result(stderr="boom"):
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


class TestStart:
    def test_always_forces_x11_vout_never_no_video(self):
        """Video output is unconditional now: VLC itself only opens a window
        when the media actually has a video track, confirmed empirically, so
        there's no reason to ever pass --no-video (would just be a redundant
        restriction, and would bring back the earlier bug of needing to
        remember a --video flag for movies)."""
        with patch.object(_player, "stop"), patch.object(
            _player.subprocess, "run", return_value=_ok_result()
        ) as mock_run, patch.object(_player, "connect"), patch.object(_player, "_vlc_path", return_value="/usr/bin/vlc"):
            start("/tmp/song.mp3")
        cmd = mock_run.call_args.args[0]
        assert "--no-video" not in cmd
        assert "--vout" in cmd
        assert cmd[cmd.index("--vout") + 1] == "x11"

    def test_same_for_a_video_file(self):
        with patch.object(_player, "stop"), patch.object(
            _player.subprocess, "run", return_value=_ok_result()
        ) as mock_run, patch.object(_player, "connect"), patch.object(_player, "_vlc_path", return_value="/usr/bin/vlc"):
            start("/tmp/movie.mp4")
        cmd = mock_run.call_args.args[0]
        assert "--no-video" not in cmd
        assert cmd[cmd.index("--vout") + 1] == "x11"

    def test_crop_passed_through_when_given(self):
        with patch.object(_player, "stop"), patch.object(
            _player.subprocess, "run", return_value=_ok_result()
        ) as mock_run, patch.object(_player, "connect"), patch.object(_player, "_vlc_path", return_value="/usr/bin/vlc"):
            start("/tmp/movie.mp4", crop="2.35:1")
        cmd = mock_run.call_args.args[0]
        assert cmd[cmd.index("--crop") + 1] == "2.35:1"

    def test_crop_omitted_when_not_given(self):
        with patch.object(_player, "stop"), patch.object(
            _player.subprocess, "run", return_value=_ok_result()
        ) as mock_run, patch.object(_player, "connect"), patch.object(_player, "_vlc_path", return_value="/usr/bin/vlc"):
            start("/tmp/movie.mp4")
        cmd = mock_run.call_args.args[0]
        assert "--crop" not in cmd

    def test_random_shuffle_is_default(self):
        """Random play must be the default so folders don't always start from
        the first file in alphabetical order."""
        with patch.object(_player, "stop"), patch.object(
            _player.subprocess, "run", return_value=_ok_result()
        ) as mock_run, patch.object(_player, "connect"), patch.object(_player, "_vlc_path", return_value="/usr/bin/vlc"):
            start("/tmp/music")
        cmd = mock_run.call_args.args[0]
        assert "--random" in cmd
        assert "--loop" not in cmd
        assert "--repeat" not in cmd

    def test_uses_plain_vlc_with_rc_as_extra_interface(self):
        """Must be plain vlc (real GUI) with RC as --extraintf, not cvlc's
        --intf rc (headless-only) -- the user wants to be able to click the
        actual on-screen VLC window's own controls, not just drive it
        through ai-vlc's CLI."""
        with patch.object(_player, "stop"), patch.object(
            _player.subprocess, "run", return_value=_ok_result()
        ) as mock_run, patch.object(_player, "connect"), patch.object(_player, "_vlc_path", return_value="/usr/bin/vlc"):
            start("/tmp/song.mp3")
        cmd = mock_run.call_args.args[0]
        assert "/usr/bin/vlc" in cmd
        assert "--extraintf" in cmd
        assert cmd[cmd.index("--extraintf") + 1] == "rc"
        assert "--intf" not in cmd

    def test_stops_existing_player_first(self):
        with patch.object(_player, "stop") as mock_stop, patch.object(
            _player.subprocess, "run", return_value=_ok_result()
        ), patch.object(_player, "connect"), patch.object(_player, "_vlc_path", return_value="/usr/bin/vlc"):
            start("/tmp/song.mp3")
        mock_stop.assert_called_once()

    def test_systemd_run_failure_raises(self):
        with patch.object(_player, "stop"), patch.object(
            _player.subprocess, "run", return_value=_fail_result("nope")
        ), patch.object(_player, "_vlc_path", return_value="/usr/bin/vlc"):
            with pytest.raises(PlayerError, match="nope"):
                start("/tmp/song.mp3")

    def test_rc_connect_failure_stops_and_raises(self):
        from ai_vlc._rc_client import RCError

        with patch.object(_player, "stop") as mock_stop, patch.object(
            _player.subprocess, "run", return_value=_ok_result()
        ), patch.object(_player, "connect", side_effect=RCError("no socket")), patch.object(
            _player, "_vlc_path", return_value="/usr/bin/vlc"
        ):
            with pytest.raises(PlayerError):
                start("/tmp/song.mp3")
        assert mock_stop.call_count == 2  # once up-front, once on the RC failure cleanup

    def test_missing_vlc_raises(self):
        with patch.object(_player, "stop"), patch("shutil.which", return_value=None):
            with pytest.raises(PlayerError, match="vlc"):
                start("/tmp/song.mp3")


class TestStop:
    def test_stop_ignores_not_loaded(self):
        with patch.object(_player.subprocess, "run", return_value=_fail_result("Unit not loaded.")):
            stop()  # should not raise

    def test_stop_raises_on_real_failure(self):
        with patch.object(_player.subprocess, "run", return_value=_fail_result("permission denied")):
            with pytest.raises(PlayerError):
                stop()
