import ssl
import unittest
from unittest.mock import MagicMock, patch

import drive


class _FakeRequest:
    def __init__(self, failures):
        self.failures = list(failures)
        self.resumable_uri = "https://upload.example/session"
        self._in_error_state = False
        self.error_state_seen = []

    def next_chunk(self):
        self.error_state_seen.append(self._in_error_state)
        if self.failures:
            raise self.failures.pop(0)
        return None, {"id": "FILE123"}


def _make_uploader(request):
    up = drive.DriveUploader.__new__(drive.DriveUploader)
    svc = MagicMock()
    svc.files.return_value.create.return_value = request
    up._svc = svc
    return up


class NetworkRetryTests(unittest.TestCase):
    def test_ssl_eof_is_network_error(self):
        self.assertTrue(drive._is_network_error(
            ssl.SSLEOFError(8, "EOF occurred in violation of protocol")))
        self.assertTrue(drive._is_network_error(ConnectionResetError()))
        self.assertFalse(drive._is_network_error(ValueError("x")))

    @patch("drive.MediaFileUpload")
    @patch("drive._wait_or_stop", return_value=True)
    def test_upload_resumes_after_ssl_eof(self, _wait, _media):
        req = _FakeRequest([ssl.SSLEOFError(8, "EOF"), ConnectionResetError()])
        up = _make_uploader(req)
        link = up.upload_video("x.mp4", "x.mp4", "folder")
        self.assertEqual(link, "https://drive.google.com/file/d/FILE123/view")
        # Lần thử lại phải hỏi trạng thái phiên resumable thay vì gửi lại từ đầu
        self.assertEqual(req.error_state_seen, [False, True, True])

    @patch("drive.MediaFileUpload")
    @patch("drive._wait_or_stop", return_value=True)
    def test_gives_up_after_network_budget(self, _wait, _media):
        n = len(drive.NETWORK_RETRY_WAITS) + 1
        req = _FakeRequest([ssl.SSLEOFError(8, "EOF")] * n)
        up = _make_uploader(req)
        with self.assertRaises(ssl.SSLEOFError):
            up.upload_video("x.mp4", "x.mp4", "folder")


if __name__ == "__main__":
    unittest.main()
