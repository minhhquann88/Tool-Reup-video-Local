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

            self.assertEqual(50, len(errors))
            self.assertEqual(main.MAX_UI_LOGS, len(normal))
            self.assertEqual(["[1/1] ! Tải lần 10/10"], retry)

            error_file = Path(tmp) / "app_error.log"
            persisted = error_file.read_text(encoding="utf-8")
            self.assertEqual(50, persisted.count("X error detail"))

    def test_parallel_video_retry_lines_do_not_overwrite_each_other(self):
        app = _make_app()
        app._log_add("[1/2] ! Tải lần 1/10", main.LOG_DOWNLOAD)
        app._log_add("[2/2] ! Tải lần 1/10", main.LOG_DOWNLOAD)
        app._log_add("[1/2] ! Tải lần 2/10", main.LOG_DOWNLOAD)

        self.assertEqual(
            [
                (main.LOG_DOWNLOAD, "[1/2] ! Tải lần 2/10"),
                (main.LOG_DOWNLOAD, "[2/2] ! Tải lần 1/10"),
            ],
            app._log_entries,
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

        self.assertEqual(
            ["! Tải lần 1/3", "! Tải lần 2/3", "! Tải lần 3/3"],
            messages,
        )


if __name__ == "__main__":
    unittest.main()
