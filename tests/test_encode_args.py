"""Tests for the x264 arguments built by VideoProcessor.process_video (no FFmpeg)."""
import os
import tempfile
import unittest
from unittest.mock import patch

import core

INFO = {"duration": 30.0, "width": 576, "height": 1024}


class _FakePopen:
    """Records the command and writes a fake output so size checks pass."""

    def __init__(self, cmd, **kwargs):
        _FakePopen.last_cmd = cmd
        with open(cmd[-1], "wb") as fh:
            fh.write(b"x" * 4096)
        self.returncode = 0

    def communicate(self, timeout=None):
        return "", ""

    def poll(self):
        return 0


class EncodeArgsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "in.mp4")
        self.out = os.path.join(self.tmp, "out.mp4")
        with open(self.src, "wb") as fh:
            fh.write(b"v")

    def _cmd(self, **settings):
        with patch.object(core.subprocess, "Popen", _FakePopen):
            core.VideoProcessor().process_video(
                self.src, self.out, settings, info=dict(INFO))
        return _FakePopen.last_cmd

    def _value(self, cmd, flag):
        return cmd[cmd.index(flag) + 1]

    def test_defaults_ultrafast_cabac_deblock_crf20(self):
        cmd = self._cmd(trim_start=1, force_1080p=True)
        self.assertEqual("libx264", self._value(cmd, "-c:v"))
        self.assertEqual("ultrafast", self._value(cmd, "-preset"))
        self.assertEqual("20", self._value(cmd, "-crf"))
        self.assertEqual("cabac=1:deblock=0,0", self._value(cmd, "-x264-params"))
        # Must follow -preset, otherwise the preset overrides it.
        self.assertGreater(cmd.index("-x264-params"), cmd.index("-preset"))
        self.assertEqual("0", self._value(cmd, "-threads"))
        self.assertEqual("yuv420p", self._value(cmd, "-pix_fmt"))

    def test_settings_override_defaults(self):
        cmd = self._cmd(trim_start=1, video_preset="veryfast", video_crf=18,
                        video_x264_params="")
        self.assertEqual("veryfast", self._value(cmd, "-preset"))
        self.assertEqual("18", self._value(cmd, "-crf"))
        self.assertNotIn("-x264-params", cmd)

    def test_stream_copy_has_no_x264_args(self):
        # Only cutting the end needs no re-encode → video is copied.
        cmd = self._cmd(trim_end=1)
        self.assertEqual("copy", self._value(cmd, "-c:v"))
        self.assertNotIn("-x264-params", cmd)
        self.assertNotIn("-preset", cmd)


if __name__ == "__main__":
    unittest.main()
