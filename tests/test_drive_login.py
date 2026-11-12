import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app_paths
import drive


def _fake_server():
    server = MagicMock()
    server.__enter__.return_value = server
    server.server_port = 12345
    return server


def _flow():
    flow = MagicMock()
    flow.authorization_url.return_value = ("https://accounts.example/x", "state")
    return flow


class DriveLoginTests(unittest.TestCase):
    def test_cancel_browser_wait_closes_server_without_exchanging_token(self):
        cancel = threading.Event()
        flow = _flow()
        server = _fake_server()
        server.handle_request.side_effect = cancel.set

        with patch("drive.HTTPServer", return_value=server), \
                self.assertRaisesRegex(RuntimeError, "Đã hủy"):
            drive._run_oauth_login(flow, lambda url: None, cancel.is_set)

        server.__exit__.assert_called_once()
        flow.fetch_token.assert_not_called()

    def test_login_link_is_shown_and_browser_is_never_opened(self):
        cancel = threading.Event()
        flow = _flow()
        server = _fake_server()
        server.handle_request.side_effect = cancel.set
        seen = []
        with patch("drive.HTTPServer", return_value=server), \
                patch("webbrowser.open") as browser_open, \
                self.assertRaisesRegex(RuntimeError, "Đã hủy"):
            drive._run_oauth_login(flow, seen.append, cancel.is_set)
        self.assertEqual(["https://accounts.example/x"], seen)
        browser_open.assert_not_called()
        flow.authorization_url.assert_called_once_with(prompt="select_account consent")
        self.assertEqual("http://localhost:12345/", flow.redirect_uri)

    def test_missing_token_never_starts_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "client_secret.json"
            secret.write_text("{}", encoding="utf-8")
            with patch("drive._secret_path", return_value=secret), \
                    patch("drive._run_oauth_login") as login:
                with self.assertRaisesRegex(RuntimeError, "Thêm tài khoản"):
                    drive._get_credentials()
                self.assertIsNone(drive._get_credentials(required=False))
                with self.assertRaisesRegex(RuntimeError, "Thêm tài khoản"):
                    drive.DriveUploader()
            login.assert_not_called()

    def test_cancelled_add_account_saves_nothing_and_next_one_works(self):
        cancel = threading.Event()
        credentials = MagicMock()
        credentials.to_json.return_value = json.dumps({"refresh_token": "r"})
        credentials.refresh_token = "r"
        attempts = []

        def login(_flow, _on_auth_url, should_stop=None):
            attempts.append(True)
            if len(attempts) == 1:
                cancel.set()
                drive._check_login_cancelled(should_stop)
            return credentials

        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "client_secret.json"
            secret.write_text("{}", encoding="utf-8")
            with patch("drive._secret_path", return_value=secret), patch(
                "drive._CREDS_LOCK", threading.Lock()
            ), patch("drive.InstalledAppFlow.from_client_secrets_file"), patch(
                "drive._run_oauth_login", side_effect=login
            ), patch("drive._fetch_email", return_value="a@gmail.com"):
                with self.assertRaisesRegex(RuntimeError, "Đã hủy"):
                    drive.add_account(lambda url: None, cancel.is_set)
                self.assertIsNone(app_paths.read_auth(drive.get_token_path())[0])
                self.assertFalse(drive._CREDS_LOCK.locked())
                self.assertEqual("a@gmail.com", drive.add_account(lambda url: None))
                self.assertEqual({"refresh_token": "r"},
                                 app_paths.read_auth(drive.get_token_path())[0])
                self.assertFalse(drive._CREDS_LOCK.locked())

    def test_cancelled_lock_wait_gives_up(self):
        cancel = threading.Event()
        cancel.set()
        lock = threading.Lock()
        lock.acquire()
        with patch("drive._secret_path") as secret, patch("drive._CREDS_LOCK", lock):
            secret.return_value.exists.return_value = True
            try:
                with self.assertRaisesRegex(RuntimeError, "Đã hủy"):
                    drive._get_credentials(cancel.is_set)
            finally:
                lock.release()


if __name__ == "__main__":
    unittest.main()
