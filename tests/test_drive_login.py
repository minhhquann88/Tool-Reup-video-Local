import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import drive


class DriveLoginCancellationTests(unittest.TestCase):
    def test_cancel_browser_wait_closes_server_without_exchanging_token(self):
        cancel = threading.Event()
        flow = MagicMock()
        flow.authorization_url.return_value = ("https://accounts.example/", "state")
        server = MagicMock()
        server.__enter__.return_value = server
        server.server_port = 12345
        server.handle_request.side_effect = cancel.set

        with patch("drive.HTTPServer", return_value=server), patch(
            "drive.webbrowser.open", return_value=True
        ), self.assertRaisesRegex(RuntimeError, "Đã hủy"):
            drive._run_oauth_login(flow, cancel.is_set)

        server.__exit__.assert_called_once()
        flow.fetch_token.assert_not_called()

    def test_cancelled_login_releases_lock_and_next_login_saves_token(self):
        cancel = threading.Event()
        credentials = MagicMock()
        credentials.to_json.return_value = '{"test": true}'
        attempts = []

        def login(_flow, should_stop):
            attempts.append(True)
            if len(attempts) == 1:
                cancel.set()
                drive._check_login_cancelled(should_stop)
            return credentials

        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "client_secret.json"
            token = Path(tmp) / "token.json"
            secret.write_text("{}", encoding="utf-8")
            with patch("drive._secret_path", return_value=secret), patch(
                "drive.get_token_path", return_value=token
            ), patch("drive._CREDS_LOCK", threading.Lock()), patch(
                "drive.InstalledAppFlow.from_client_secrets_file"
            ), patch("drive._run_oauth_login", side_effect=login):
                with self.assertRaisesRegex(RuntimeError, "Đã hủy"):
                    drive._get_credentials(cancel.is_set)
                self.assertFalse(token.exists())
                self.assertFalse(drive._CREDS_LOCK.locked())
                result = drive._get_credentials(threading.Event().is_set)
                self.assertIs(result, credentials)
                self.assertEqual('{"test": true}', token.read_text(encoding="utf-8"))
                self.assertFalse(drive._CREDS_LOCK.locked())

    def test_cancelled_lock_wait_does_not_start_browser(self):
        cancel = threading.Event()
        cancel.set()
        lock = threading.Lock()
        lock.acquire()
        with patch("drive._secret_path") as secret, patch(
            "drive._CREDS_LOCK", lock
        ), patch("drive._run_oauth_login") as login:
            secret.return_value.exists.return_value = True
            try:
                with self.assertRaisesRegex(RuntimeError, "Đã hủy"):
                    drive._get_credentials(cancel.is_set)
                login.assert_not_called()
            finally:
                lock.release()


if __name__ == "__main__":
    unittest.main()
