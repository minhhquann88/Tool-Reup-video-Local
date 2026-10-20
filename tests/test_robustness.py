import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import error_text
import license as license_mod
import main
import tts


class ReadTableTests(unittest.TestCase):
    def _write(self, tmp, name, data: bytes) -> str:
        path = os.path.join(tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_csv_cp1258_blank_rows_extra_cells_and_header_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = (" item_id , video_url ,product_name\r\n"
                    "1,http://a,Kem\r\n"
                    ",,\r\n"
                    "2,http://b,Son,EXTRA,MORE\r\n")
            path = self._write(tmp, "in.csv", text.encode("cp1258"))
            rows = main._read_table(path)
        self.assertEqual(2, len(rows))
        self.assertEqual("http://a", rows[0]["video_url"])
        self.assertNotIn(None, rows[1])
        self.assertEqual("Son", rows[1]["product_name"])

    def test_xls_gives_clear_message(self):
        with self.assertRaisesRegex(ValueError, ".xlsx"):
            main._read_table("old.xls")


class WriteOutputCsvTests(unittest.TestCase):
    def _app(self):
        return object.__new__(main.App)

    def test_extra_none_key_and_union_of_columns(self):
        videos = [{"item_id": "1", "video_url": "u1"},
                  {"item_id": "2", "video_url": "u2", "note": "x", None: ["junk"]}]
        refs = {id(videos[0]): "link1", id(videos[1]): "Lỗi"}
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.csv")
            saved = self._app()._write_output_csv(out, refs, videos)
            with open(saved, encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(out, saved)
        self.assertEqual(["link1", "Lỗi"], [r["Link Video"] for r in rows])
        self.assertIn("note", rows[0])

    def test_locked_output_falls_back_to_new_file(self):
        videos = [{"item_id": "1", "video_url": "u1"}]
        real_replace = os.replace

        def _replace(src, dst):
            if dst.endswith("out.csv"):
                raise PermissionError(13, "file đang mở trong Excel")
            return real_replace(src, dst)

        with tempfile.TemporaryDirectory() as tmp, \
                patch("main.os.replace", side_effect=_replace), \
                patch("main.time.sleep", return_value=None):
            out = os.path.join(tmp, "out.csv")
            saved = self._app()._write_output_csv(out, {id(videos[0]): "L"}, videos)
            self.assertNotEqual(out, saved)
            self.assertTrue(os.path.exists(saved))


class SafeFilenameTests(unittest.TestCase):
    def test_strips_windows_forbidden_characters(self):
        self.assertEqual("abc_1", main._safe_filename("abc:1"))
        self.assertEqual("a_b_c", main._safe_filename("a/b\\c"))
        self.assertEqual("", main._safe_filename(" .. "))


class LicenseNetworkTests(unittest.TestCase):
    def test_network_error_keeps_saved_key(self):
        with patch("license.load_saved_key", return_value="KEY"), \
                patch("license.validate_key",
                      return_value={"status": 0, "network_error": True}), \
                patch("license.clear_key") as clear:
            result = license_mod.check_license()
        clear.assert_not_called()
        self.assertTrue(result["network_error"])
        self.assertFalse(result["is_valid"])

    def test_invalid_key_is_cleared(self):
        with patch("license.load_saved_key", return_value="KEY"), \
                patch("license.validate_key",
                      return_value={"status": 0, "message": "Key sai"}), \
                patch("license.clear_key") as clear:
            license_mod.check_license()
        clear.assert_called_once()


class TtsRetryTests(unittest.TestCase):
    def test_permanent_and_slowdown_classification(self):
        self.assertTrue(tts._is_permanent_error(RuntimeError("Gemini API HTTP 401:\n{}")))
        self.assertFalse(tts._is_permanent_error(RuntimeError("Gemini API HTTP 429:\n{}")))
        self.assertEqual(2, tts._retry_wait(RuntimeError("rỗng"), 2, 3))
        self.assertEqual(16, tts._retry_wait(RuntimeError("HTTP 503"), 2, 3))
        self.assertEqual(60, tts._retry_wait(RuntimeError("HTTP 429"), 2, 6))

    def test_wrong_key_is_not_retried(self):
        calls = []

        class _Resp:
            status_code = 403
            text = '{"error": {"message": "API key not valid"}}'

            def json(self):
                return {"error": {"message": "API key not valid"}}

        def _post(*_a, **_k):
            calls.append(1)
            return _Resp()

        with patch("tts.requests.post", side_effect=_post), \
                patch("tts._sleep_or_stop", return_value=None):
            with self.assertRaises(RuntimeError):
                tts.generate_script("{product_name}", {"product_name": "x"},
                                    api_key="k", retries=7)
        self.assertEqual(1, len(calls))


class StageDetectionTests(unittest.TestCase):
    def test_ffmpeg_stderr_with_audio_is_ffmpeg(self):
        exc = RuntimeError("FFmpeg lỗi (exit code 1):\nStream #0:1: Audio: aac\nInvalid data")
        self.assertEqual("FFMPEG-ERR", error_text.explain(exc).code)

    def test_ffmpeg_small_output_is_not_voice(self):
        exc = RuntimeError("FFmpeg output quá nhỏ (100 byte) — có thể bị cắt")
        self.assertEqual("FFMPEG-ERR", error_text.explain(exc).code)

    def test_drive_hosted_source_403_is_download_error(self):
        import requests
        resp = requests.Response()
        resp.status_code = 403
        inner = requests.HTTPError(
            "403 Client Error: Forbidden for url: https://drive.usercontent.google.com/x",
            response=resp)
        try:
            try:
                raise inner
            except requests.HTTPError as e:
                raise RuntimeError(f"Tải video thất bại sau 1 lần thử: {e}") from e
        except RuntimeError as exc:
            self.assertEqual("DL-403", error_text.explain(exc).code)

    def test_mid_download_disconnect_is_network(self):
        import requests
        exc = requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead")
        self.assertEqual("NET-DL", error_text.explain(exc, "DL").code)


if __name__ == "__main__":
    unittest.main()
