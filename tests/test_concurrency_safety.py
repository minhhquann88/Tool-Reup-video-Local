import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
import core
import drive


class BatchSnapshotTests(unittest.TestCase):
    def test_output_csv_uses_batch_snapshot_not_live_ui_rows(self):
        app = object.__new__(main.App)
        app._videos = [{"item_id": "new", "video_url": "new-url"}]
        snapshot = [{"item_id": "old", "video_url": "old-url"}]

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output.csv"
            app._write_output_csv(str(output), {id(snapshot[0]): "saved.mp4"}, snapshot)

            with output.open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual([], list(Path(tmp).glob("output.csv.*.tmp")))

        self.assertEqual("old", rows[0]["item_id"])
        self.assertEqual("saved.mp4", rows[0]["Link Video"])


class VideoProcessorCancellationTests(unittest.TestCase):
    def test_cancel_active_terminates_registered_processes(self):
        class FakeProcess:
            def __init__(self):
                self.terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

        processor = main.VideoProcessor()
        process = FakeProcess()
        with processor._process_lock:
            processor._active_processes.add(process)

        processor.cancel_active()

        self.assertTrue(process.terminated)


class DownloadCancellationTests(unittest.TestCase):
    def test_cancel_during_stream_closes_response_and_removes_partial_file(self):
        class FakeResponse:
            headers = {"Content-Type": "video/mp4", "content-length": "2048"}

            def __init__(self):
                self.closed = False

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield b"x" * chunk_size

            def close(self):
                self.closed = True

        class FakeSession:
            def __init__(self, response):
                self.response = response

            def get(self, *_args, **_kwargs):
                return self.response

        response = FakeResponse()
        downloader = object.__new__(core.VideoDownloader)
        downloader.session = FakeSession(response)
        checks = iter((False, True))

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "partial.mp4"
            with self.assertRaisesRegex(RuntimeError, "Đã dừng"):
                downloader.download(
                    "https://example.test/video.mp4", str(output), retries=1,
                    should_stop=lambda: next(checks, True),
                )
            self.assertFalse(output.exists())

        self.assertTrue(response.closed)


class DriveTokenTests(unittest.TestCase):
    def test_valid_saved_token_does_not_start_oauth_browser(self):
        class FakeCredentials:
            valid = True
            expired = False
            refresh_token = None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / "client_secret.json"
            token = root / "token.json"
            secret.write_text("{}", encoding="utf-8")
            token.write_text("saved-token", encoding="utf-8")

            with patch("drive._secret_path", return_value=secret), patch(
                "drive.get_token_path", return_value=token
            ), patch(
                "drive.Credentials.from_authorized_user_file",
                return_value=FakeCredentials(),
            ), patch("drive.InstalledAppFlow.from_client_secrets_file") as flow:
                result = drive._get_credentials()

        self.assertTrue(result.valid)
        flow.assert_not_called()


class DriveRetryTests(unittest.TestCase):
    def test_only_rate_limit_403_is_retryable(self):
        class Response:
            def __init__(self, status):
                self.status = status

        class Error(Exception):
            def __init__(self, status, content):
                self.resp = Response(status)
                self.content = content

        self.assertTrue(drive._is_retryable_drive_error(
            Error(403, b'{"reason":"userRateLimitExceeded"}')
        ))
        self.assertTrue(drive._is_retryable_drive_error(Error(429, b"")))
        self.assertFalse(drive._is_retryable_drive_error(
            Error(403, b'{"reason":"insufficientFilePermissions"}')
        ))


if __name__ == "__main__":
    unittest.main()
