import errno
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import error_text
from error_text import explain


def _raise_from(outer, inner):
    try:
        try:
            raise inner
        except BaseException as exc:
            raise outer from exc
    except BaseException as exc:
        return exc


class ExplainTests(unittest.TestCase):
    def test_ssl_eof_during_drive_upload(self):
        exc = ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:2406)")
        with patch("error_text._tb_files", return_value="drive.py googleapiclient/http.py"):
            info = explain(exc)
        self.assertEqual("NET-DRIVE", info.code)
        self.assertIn("Mất kết nối mạng", info.cause)
        self.assertIn("SSLEOFError", info.detail)

    def test_download_failure_wraps_network_error(self):
        exc = _raise_from(RuntimeError("Tải video thất bại sau 7 lần thử: boom"),
                          requests.ConnectionError("boom"))
        self.assertEqual("NET-DL", explain(exc).code)

    def test_expired_download_link(self):
        resp = requests.Response()
        resp.status_code = 403
        inner = requests.HTTPError("403 Client Error: Forbidden for url", response=resp)
        exc = _raise_from(RuntimeError("Tải video thất bại sau 7 lần thử: x"), inner)
        self.assertEqual("DL-403", explain(exc).code)

    def test_ai_key_and_rate_limit(self):
        self.assertEqual("KEY-AI", explain(RuntimeError(
            "Gemini thất bại sau 7 lần: Gemini API HTTP 401:\n{...}")).code)
        self.assertEqual("LIMIT-AI", explain(RuntimeError(
            "ChatGPT thất bại sau 7 lần: OpenAI API HTTP 429:\n{...}")).code)
        self.assertEqual("CFG-KEY-AI", explain(ValueError("Thiếu API key Gemini")).code)

    def test_voice_server_error(self):
        self.assertEqual("SERVER-VOICE", explain(RuntimeError(
            "AutoVoice API thất bại sau 7 lần: AutoVoice API HTTP 502:\n<html>")).code)

    def test_ffmpeg_errors(self):
        self.assertEqual("FFMPEG-ERR", explain(RuntimeError(
            "FFmpeg lỗi (exit code 1):\nInvalid data found")).code)
        self.assertEqual("FFMPEG-TIMEOUT", explain(RuntimeError(
            "FFmpeg quá thời hạn 10 phút\nStderr:\n...")).code)

    def test_disk_full_and_permission(self):
        self.assertEqual("DISK-FULL", explain(OSError(errno.ENOSPC, "No space left")).code)
        self.assertEqual("FILE-PERM", explain(PermissionError(13, "denied")).code)

    def test_unrelated_context_is_ignored(self):
        try:
            try:
                raise RuntimeError("Đã dừng theo yêu cầu")
            except RuntimeError:
                raise RuntimeError("Google TTS API HTTP 403:\n{}")
        except RuntimeError as exc:
            self.assertEqual("KEY-VOICE", explain(exc).code)

    def test_stage_hint(self):
        self.assertEqual("UNKNOWN-DL", explain(OSError("x"), "DL").code)

    def test_unknown_error_asks_to_report(self):
        info = explain(KeyError("x"))
        self.assertTrue(info.code.startswith("UNKNOWN"))
        self.assertIn("app_error.log", info.fix)

    def test_detail_is_single_short_line(self):
        info = explain(RuntimeError("FFmpeg lỗi (exit code 1):\n" + "x" * 5000))
        self.assertNotIn("\n", info.detail)
        self.assertLessEqual(len(info.detail), 160)


class LogExceptionTests(unittest.TestCase):
    def test_ui_shows_summary_file_keeps_traceback(self):
        import main

        app = main.App.__new__(main.App)
        shown = []
        app._log_msg = lambda msg, cat=None: shown.append(msg)
        exc = subprocess.CalledProcessError(1, "ffmpeg", stderr=b"ffmpeg stderr detail")
        with tempfile.TemporaryDirectory() as tmp, patch(
            "main._crash_dir", return_value=Path(tmp)
        ):
            try:
                raise exc
            except subprocess.CalledProcessError as e:
                app._log_exception("[1/1] Lỗi video", e)
            persisted = (Path(tmp) / "app_error.log").read_text(encoding="utf-8")

        msg, = shown
        self.assertNotIn("Traceback", msg)
        self.assertIn("• Lỗi:", msg)
        self.assertIn("• Cách xử lý:", msg)
        self.assertIn("Traceback", persisted)
        self.assertIn("ffmpeg stderr detail", persisted)


if __name__ == "__main__":
    unittest.main()
