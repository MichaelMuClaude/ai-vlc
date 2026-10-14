"""Tests for ai_vlc._system_volume, with subprocess mocked out."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from ai_vlc._system_volume import SystemVolumeError, get_system_volume, set_system_volume


def _fake_result(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestGetSystemVolume:
    def test_parses_unmuted_volume(self):
        with patch("subprocess.run", return_value=_fake_result(stdout="Volume: 0.55\n")):
            info = get_system_volume()
        assert info == {"percent": 55, "muted": False}

    def test_parses_muted_volume(self):
        with patch("subprocess.run", return_value=_fake_result(stdout="Volume: 0.55 [MUTED]\n")):
            info = get_system_volume()
        assert info == {"percent": 55, "muted": True}

    def test_rounds_percent(self):
        with patch("subprocess.run", return_value=_fake_result(stdout="Volume: 0.17\n")):
            info = get_system_volume()
        assert info["percent"] == 17

    def test_nonzero_exit_raises(self):
        with patch("subprocess.run", return_value=_fake_result(stderr="boom", returncode=1)):
            with pytest.raises(SystemVolumeError):
                get_system_volume()

    def test_unparseable_output_raises(self):
        with patch("subprocess.run", return_value=_fake_result(stdout="garbage")):
            with pytest.raises(SystemVolumeError):
                get_system_volume()

    def test_missing_wpctl_raises(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemVolumeError):
                get_system_volume()


class TestSetSystemVolume:
    def test_sets_and_returns_new_state(self):
        with patch("subprocess.run", return_value=_fake_result(stdout="Volume: 0.70\n")) as mock_run:
            info = set_system_volume(70)
        assert info == {"percent": 70, "muted": False}
        # Three subprocess calls: set-mute, set-volume, then get-volume to confirm.
        assert mock_run.call_count == 3
        assert mock_run.call_args_list[0].args[0][1:] == ["set-mute", "@DEFAULT_AUDIO_SINK@", "0"]
        assert mock_run.call_args_list[1].args[0][1:] == ["set-volume", "@DEFAULT_AUDIO_SINK@", "70%"]

    def test_set_failure_raises(self):
        with patch("subprocess.run", return_value=_fake_result(stderr="boom", returncode=1)):
            with pytest.raises(SystemVolumeError):
                set_system_volume(70)

    def test_unmute_is_best_effort_and_does_not_fail_flow(self):
        def fake_run(args, **kwargs):
            if args[1] == "set-mute":
                return _fake_result(stderr="no such sink", returncode=1)
            return _fake_result(stdout="Volume: 0.70\n")

        with patch("subprocess.run", side_effect=fake_run) as mock_run:
            info = set_system_volume(70)
        assert info == {"percent": 70, "muted": False}
        assert mock_run.call_count == 3
        assert mock_run.call_args_list[0].args[0][1:] == ["set-mute", "@DEFAULT_AUDIO_SINK@", "0"]
