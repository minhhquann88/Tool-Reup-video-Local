import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core
import main


class _Filter:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _TextView:
    def yview(self):
        return (0.0, 1.0)


class _Textbox:
    def __init__(self):
        self.content = ""
        self._textbox = _TextView()

    def configure(self, **_kwargs):
        pass

    def insert(self, _position, text):
        self.content += text

    def delete(self, _start, _end):
        self.content = ""

    def see(self, _position):
        pass


def _make_app():
    app = object.__new__(main.App)
    app._log_entries = []
    app._log_filter = _Filter(main.LOG_FILTER_ALL)
    app._log = _Textbox()
    app._ui = lambda callback: callback()
    return app


class LogOptimizationTests(unittest.TestCase):
    def test_crash_log_is_appended_instead_of_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "main._crash_dir", return_value=Path(tmp)
        ):
            main._write_crash("first traceback")
            main._write_crash("second traceback")

            persisted = (Path(tmp) / "crash.log").read_text(encoding="utf-8")
            self.assertIn("first traceback", persisted)
            self.assertIn("second traceback", persisted)

    def test_errors_are_kept_normal_logs_are_capped_and_retry_is_updated(self):
        app = _make_app()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "main._crash_dir", return_value=Path(tmp)
        ):
            for index in range(50):
                app._log_add(f"X error detail {index}", main.LOG_ERROR)
            for index in range(1500):
                app._log_add(f"progress {index}", main.LOG_PROCESS)
            for attempt in range(1, 11):
                app._log_add(
                    f"[1/1] ! Tải lần {attempt}/10", main.LOG_DOWNLOAD
                )

            errors = [
                text for cat, text in app._log_entries if cat == main.LOG_ERROR
            ]
            normal = [
                text for cat, text in app._log_entries if cat != main.LOG_ERROR
            ]
            retry = [text for text in normal if "Tải lần" in text]

            # UI chỉ giữ MAX_UI_LOGS dòng mới nhất (tính cả lỗi); lỗi đầy đủ
            # vẫn nằm trong app_error.log.
            self.assertEqual([], errors)
            self.assertEqual(main.MAX_UI_LOGS, len(normal))
            self.assertEqual(1, len(retry))
            self.assertTrue(retry[0].endswith("[1/1] ! Tải lần 10/10"))

            error_file = Path(tmp) / "app_error.log"
            persisted = error_file.read_text(encoding="utf-8")
            self.assertEqual(50, persisted.count("X error detail"))

    def test_parallel_video_retry_lines_do_not_overwrite_each_other(self):
        app = _make_app()
        app._log_add("[1/2] ! Tải lần 1/10", main.LOG_DOWNLOAD)
        app._log_add("[2/2] ! Tải lần 1/10", main.LOG_DOWNLOAD)
        app._log_add("[1/2] ! Tải lần 2/10", main.LOG_DOWNLOAD)

        # Bỏ tiền tố giờ "[HH:MM:SS] " trước khi so sánh.
        entries = [(cat, text[11:]) for cat, text in app._log_entries]
        self.assertEqual(
            [
                (main.LOG_DOWNLOAD, "[1/2] ! Tải lần 2/10"),
                (main.LOG_DOWNLOAD, "[2/2] ! Tải lần 1/10"),
            ],
            entries,
        )


class DownloadRetryFormatTests(unittest.TestCase):
    def test_downloader_emits_stable_retry_messages(self):
        class _FailingSession:
            def get(self, *_args, **_kwargs):
                raise OSError("network detail that changes")

        downloader = object.__new__(core.VideoDownloader)
        downloader.session = _FailingSession()
        messages = []

        with tempfile.TemporaryDirectory() as tmp, patch(
            "core.time.sleep", return_value=None
        ):
            output = str(Path(tmp) / "video.mp4")
            with self.assertRaisesRegex(RuntimeError, "network detail that changes"):
                downloader.download(output, output, retries=3, log=messages.append)

        self.assertEqual(3, len(messages))
        for attempt, message in enumerate(messages, 1):
            self.assertTrue(message.startswith(f"! Tải lần {attempt}/3 lỗi: "))
            # Thông báo thử lại là câu tiếng Việt ngắn, không lộ chi tiết thô.
            self.assertNotIn("network detail", message)

    def test_expired_tiktok_link_403_is_explained_and_not_retried(self):
        import requests

        calls = []

        class _Forbidden:
            def get(self, *_args, **_kwargs):
                calls.append(1)
                resp = requests.Response()
                resp.status_code = 403
                raise requests.HTTPError("403 Client Error", response=resp)

        downloader = object.__new__(core.VideoDownloader)
        downloader.session = _Forbidden()
        # 0x6ad32e14 = 17/10/2026 15:13 — đã qua.
        url = ("https://v16m.tiktokcdn-us.com/" + "a" * 32
               + "/6ad32e14/video/tos/x/?a=1")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "Link video đã hết hạn lúc"):
                downloader.download(url, str(Path(tmp) / "v.mp4"))
        self.assertEqual(1, len(calls))
        self.assertEqual(0x6ad32e14, core.link_expiry(url))

    def test_http_403_is_not_retried(self):
        import requests

        calls = []

        class _Forbidden:
            def get(self, *_args, **_kwargs):
                calls.append(1)
                resp = requests.Response()
                resp.status_code = 403
                raise requests.HTTPError("403 Client Error", response=resp)

        downloader = object.__new__(core.VideoDownloader)
        downloader.session = _Forbidden()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "core.time.sleep", return_value=None
        ):
            with self.assertRaises(RuntimeError):
                downloader.download("https://x/v.mp4", str(Path(tmp) / "v.mp4"),
                                    retries=7)
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
