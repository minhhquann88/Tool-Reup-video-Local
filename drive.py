"""
drive.py - Google Drive uploader using OAuth 2.0 (Desktop app credentials).

First run: opens browser for Google login → saves token.json next to the exe.
Subsequent runs: uses saved token automatically (refreshes if expired).

Files are uploaded to the authenticated user's own Drive → no quota issue.
"""

from __future__ import annotations

import os
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


def _is_retryable_drive_error(exc: Exception) -> bool:
    """Retry only transient/rate-limit errors; never hide auth or ACL errors."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in (429, 500, 502, 503, 504):
        return True
    if status != 403:
        return False
    content = getattr(exc, "content", b"")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="ignore")
    detail = str(content or exc).casefold()
    return "userratelimitexceeded" in detail or "ratelimitexceeded" in detail


def _wait_or_stop(seconds: float, should_stop=None) -> bool:
    """Wait interruptibly.  Returns False when cancellation was requested."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return False
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    return not should_stop or not should_stop()


def _bundle_dir() -> Path:
    """Where bundled files (client_secret.json) live — sys._MEIPASS when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _exe_dir() -> Path:
    """
    Where runtime files (token.json) live — a WRITABLE folder.
    - AppImage: mount is read-only → write next to the .AppImage file
      ($APPIMAGE); fall back to ~/.config/VideoReupTool if not writable.
    - Other frozen (Windows exe / onedir): next to the executable.
    - Dev: next to this source file.
    """
    appimg = os.environ.get("APPIMAGE")
    if appimg:
        here = Path(appimg).parent
        if os.access(str(here), os.W_OK):
            return here
        import license as license_mod
        _cfg_name = "RenderVideoReupPro" if getattr(license_mod, "APP_ID", "") == "tool_reup_video_pro" else "RenderVideoReup"
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / _cfg_name
        try:
            cfg.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return cfg
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

    def _read_token() -> Credentials | None:
        if not token.exists():
            return None
        try:
            return Credentials.from_authorized_user_file(str(token), SCOPES)
        except Exception:
            # A prior crash can leave a truncated token.  Treat it as absent so
            # the user can authenticate again instead of blocking every worker.
            return None

    def _write_token(value: Credentials) -> None:
        tmp = token.with_name(f"{token.name}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(value.to_json(), encoding="utf-8")
            os.replace(tmp, token)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # Reading and rewriting token.json must be one critical section: a worker
    # must not parse the file while another worker is refreshing credentials.
    with _CREDS_LOCK:
        creds = _read_token()
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _write_token(creds)
            return creds

    # Serialize first-time OAuth too.  Opening many browser windows and letting
    # the last token writer win is confusing and can leave worker startup in an
    # indeterminate state.  Recheck the token after obtaining the lock because
    # another login may have completed while this caller was waiting.
    with _CREDS_LOCK:
        creds = _read_token()
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _write_token(creds)
            return creds

        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(
            port=0,
            timeout_seconds=600,
            open_browser=True,
            success_message=(
                "Đăng nhập thành công! Bạn có thể đóng tab này và quay lại tool."
            ),
        )
        _write_token(creds)

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

    def create_folder(self, name: str, should_stop=None, log=None) -> str:
        """Create a folder in My Drive. Returns folder ID."""
        meta   = {"name": name, "mimeType": MIME_DIR}
        for attempt in range(7):
            if should_stop and should_stop():
                raise RuntimeError("Đã dừng theo yêu cầu")
            try:
                folder = self._svc.files().create(body=meta, fields="id").execute()
                return folder["id"]
            except Exception as exc:
                if not _is_retryable_drive_error(exc) or attempt == 6:
                    raise
                wait = 2.0   # chờ cố định 2s giữa các lần thử
                if log:
                    log(f"! Drive quá tải, thử tạo folder lại sau {wait:.1f}s "
                        f"({attempt + 1}/7)")
                if not _wait_or_stop(wait, should_stop):
                    raise RuntimeError("Đã dừng theo yêu cầu")

        raise RuntimeError("Không thể tạo folder trên Drive")

    def upload_video(self, local_path: str, filename: str,
                     folder_id: str, progress_cb=None, should_stop=None,
                     log=None) -> str:
        """Upload video, make public, return shareable view link."""
        meta    = {"name": filename, "parents": [folder_id]}
        media   = MediaFileUpload(local_path, mimetype=MIME_MP4, resumable=True)
        request = self._svc.files().create(
            body=meta, media_body=media, fields="id"
        )

        response = None
        retry_attempt = 0
        while response is None:
            if should_stop and should_stop():
                raise RuntimeError("Đã dừng theo yêu cầu")
            try:
                status, response = request.next_chunk()
                retry_attempt = 0
            except Exception as exc:
                if not _is_retryable_drive_error(exc) or retry_attempt >= 6:
                    raise
                wait = 2.0   # chờ cố định 2s giữa các lần thử
                retry_attempt += 1
                if log:
                    log(f"! Drive quá tải, thử upload lại sau {wait:.1f}s "
                        f"({retry_attempt}/7)")
                if not _wait_or_stop(wait, should_stop):
                    raise RuntimeError("Đã dừng theo yêu cầu")
                continue
            if status and progress_cb:
                progress_cb(status.progress())

        if progress_cb:
            progress_cb(1.0)

        file_id = response["id"]

        # Permission requests are also rate-limited.  Retry only documented
        # transient/rate errors with a fixed 2s delay.
        for attempt in range(7):
            if should_stop and should_stop():
                raise RuntimeError("Đã dừng theo yêu cầu")
            try:
                self._svc.permissions().create(
                    fileId=file_id,
                    body={"role": "reader", "type": "anyone"},
                ).execute()
                break
            except Exception as exc:
                if not _is_retryable_drive_error(exc) or attempt == 6:
                    raise
                wait = 2.0   # chờ cố định 2s giữa các lần thử
                if log:
                    log(f"! Drive quá tải, thử cấp quyền lại sau {wait:.1f}s "
                        f"({attempt + 1}/7)")
                if not _wait_or_stop(wait, should_stop):
                    raise RuntimeError("Đã dừng theo yêu cầu")

        return f"https://drive.google.com/file/d/{file_id}/view"
