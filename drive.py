"""
drive.py - Google Drive uploader using OAuth 2.0 (Desktop app credentials).

First run: opens browser for Google login → saves token.json next to the exe.
Subsequent runs: uses saved token automatically (refreshes if expired).

Files are uploaded to the authenticated user's own Drive → no quota issue.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES   = ["https://www.googleapis.com/auth/drive"]
MIME_DIR = "application/vnd.google-apps.folder"
MIME_MP4 = "video/mp4"

_CREDS_LOCK = threading.Lock()


def _bundle_dir() -> Path:
    """Where bundled files (client_secret.json) live — sys._MEIPASS when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _exe_dir() -> Path:
    """Where runtime files (token.json) live — writable folder next to exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


# Public path so main.py can reference it for logout
def get_token_path() -> Path:
    return _exe_dir() / "token.json"


def _secret_path() -> Path:
    return _bundle_dir() / "client_secret.json"


def _get_credentials() -> Credentials:
    """
    Return valid OAuth credentials (thread-safe).
    - token.json exists and valid  → reuse silently.
    - token.json expired           → refresh silently.
    - No token yet                 → open browser for first-time login (120s timeout).
    """
    secret = _secret_path()
    if not secret.exists():
        raise FileNotFoundError(
            f"Không tìm thấy:\n{secret}\n\n"
            "Đổi tên file client_secret_xxx.json thành client_secret.json\n"
            "và đặt cạnh file VideoReupTool.exe (hoặc main.py khi dev)."
        )

    token = get_token_path()
    creds: Credentials | None = None

    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        with _CREDS_LOCK:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding="utf-8")
        return creds

    # Browser login — do NOT hold lock here; this blocks until user completes
    # (or 120 s timeout). Multiple concurrent logins are fine: each gets its
    # own local server port, last writer wins for token.json.
    flow  = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0, timeout_seconds=120)

    with _CREDS_LOCK:
        token.write_text(creds.to_json(), encoding="utf-8")

    return creds


class DriveUploader:

    def __init__(self):
        creds     = _get_credentials()
        self._svc = build("drive", "v3", credentials=creds,
                          cache_discovery=False)

    def test_connection(self) -> str:
        """Returns the logged-in user's email."""
        about = self._svc.about().get(fields="user").execute()
        return about["user"]["emailAddress"]

    def create_folder(self, name: str) -> str:
        """Create a folder in My Drive. Returns folder ID."""
        meta   = {"name": name, "mimeType": MIME_DIR}
        folder = self._svc.files().create(body=meta, fields="id").execute()
        return folder["id"]

    def upload_video(self, local_path: str, filename: str,
                     folder_id: str, progress_cb=None) -> str:
        """Upload video, make public, return shareable view link."""
        meta    = {"name": filename, "parents": [folder_id]}
        media   = MediaFileUpload(local_path, mimetype=MIME_MP4, resumable=True)
        request = self._svc.files().create(
            body=meta, media_body=media, fields="id"
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and progress_cb:
                progress_cb(status.progress())

        if progress_cb:
            progress_cb(1.0)

        file_id = response["id"]

        # Google Drive permissions API can return transient 500 errors — retry
        for attempt in range(4):
            try:
                self._svc.permissions().create(
                    fileId=file_id,
                    body={"role": "reader", "type": "anyone"},
                ).execute()
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1)

        return f"https://drive.google.com/file/d/{file_id}/view"
