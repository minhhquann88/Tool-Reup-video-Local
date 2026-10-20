"""
drive.py - Google Drive uploader using OAuth 2.0 (Desktop app credentials).

First run: opens browser for Google login → saves token.json next to the exe.
Subsequent runs: uses saved token automatically (refreshes if expired).

Files are uploaded to the authenticated user's own Drive → no quota issue.
"""

from __future__ import annotations

import http.client
import os
import socket
import ssl
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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


# Lỗi tầng mạng (mất kết nối, TLS bị cắt ngang, timeout...).  Không phải lỗi
# của Drive nên không tính vào ngân sách retry rate-limit; dùng ngân sách riêng:
# tối đa 5 lần, chờ cố định 2s giữa các lần thử.
_NETWORK_ERRORS = (
    ssl.SSLError,            # gồm SSLEOFError, SSLZeroReturnError...
    ConnectionError,         # ConnectionReset/Aborted/Refused, BrokenPipe
    socket.timeout,
    TimeoutError,
    http.client.HTTPException,  # RemoteDisconnected, IncompleteRead...
)
NETWORK_RETRY_WAITS = (2.0,) * 5


def _is_network_error(exc: Exception) -> bool:
    if isinstance(exc, _NETWORK_ERRORS):
        return True
    # Làm mới token giữa lúc upload mà mất mạng → TransportError.
    try:
        from google.auth.exceptions import TransportError
        if isinstance(exc, TransportError):
            return True
    except ImportError:  # pragma: no cover
        pass
    # OSError chung (ENETUNREACH, EHOSTUNREACH...) — trừ lỗi file cục bộ.
    if (isinstance(exc, OSError) and not hasattr(exc, "resp")
            and not isinstance(exc, (FileNotFoundError, PermissionError,
                                     IsADirectoryError))):
        return True
    try:
        import httplib2
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, (httplib2.ServerNotFoundError,
                            httplib2.HttpLib2Error)) and not hasattr(exc, "resp")


def _reset_connections(svc) -> None:
    """Bỏ các kết nối HTTP đã hỏng để lần thử sau mở kết nối TLS mới."""
    http = getattr(svc, "_http", None)
    inner = getattr(http, "http", http)   # AuthorizedHttp -> httplib2.Http
    conns = getattr(inner, "connections", None)
    if not isinstance(conns, dict):
        return
    for conn in list(conns.values()):
        try:
            conn.close()
        except Exception:
            pass
    conns.clear()


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


def _check_login_cancelled(should_stop=None) -> None:
    if should_stop and should_stop():
        raise RuntimeError("Đã hủy đăng nhập Google Drive")


def _run_oauth_login(flow, should_stop=None) -> Credentials:
    """Wait for the browser callback while allowing a new login to cancel us."""
    callback = []

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            if parsed.path != "/" or not ("code" in params or "error" in params):
                self.send_error(404)
                return
            callback.append(flow.redirect_uri + "?" + parsed.query)
            body = "Đã nhận phản hồi Google. Hãy quay lại tool để xem kết quả đăng nhập.".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass  # Closing the browser tab must not discard the callback.

        def log_message(self, *_args):
            pass  # Never log the OAuth authorization code.

    with HTTPServer(("127.0.0.1", 0), CallbackHandler) as server:
        server.timeout = 0.2
        flow.redirect_uri = f"http://localhost:{server.server_port}/"
        auth_url, _ = flow.authorization_url()
        _check_login_cancelled(should_stop)
        if not webbrowser.open(auth_url, new=1, autoraise=True):
            raise RuntimeError("Không mở được trình duyệt. Hãy mở trình duyệt mặc định rồi đăng nhập lại.")
        deadline = time.monotonic() + 600
        while not callback:
            _check_login_cancelled(should_stop)
            if time.monotonic() >= deadline:
                raise RuntimeError("Hết thời gian đăng nhập Google Drive. Hãy bấm Đăng nhập để thử lại.")
            server.handle_request()

    _check_login_cancelled(should_stop)
    # OAuthlib requires HTTPS when parsing the loopback authorization response.
    flow.fetch_token(authorization_response=callback[0].replace("http://", "https://", 1),
                     timeout=30)
    _check_login_cancelled(should_stop)
    return flow.credentials


def _get_credentials(should_stop=None) -> Credentials:
    """
    Return valid OAuth credentials (thread-safe).
    - token.json exists and valid  → reuse silently.
    - token.json expired           → refresh silently.
    - No token yet                 → open browser for first-time login (600s timeout).
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

    # Serialize token access and OAuth, but let superseded login attempts stop
    # both while waiting for the lock and while waiting for the browser.
    while not _CREDS_LOCK.acquire(timeout=0.2):
        _check_login_cancelled(should_stop)
    try:
        _check_login_cancelled(should_stop)
        creds = _read_token()
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _check_login_cancelled(should_stop)
            _write_token(creds)
            return creds

        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = _run_oauth_login(flow, should_stop)
        _check_login_cancelled(should_stop)
        _write_token(creds)
    finally:
        _CREDS_LOCK.release()

    return creds


class DriveUploader:

    def __init__(self, should_stop=None):
        creds     = _get_credentials(should_stop)
        _check_login_cancelled(should_stop)
        self._svc = build("drive", "v3", credentials=creds,
                          cache_discovery=False)

    def test_connection(self) -> str:
        """Returns the logged-in user's email."""
        about = self._svc.about().get(fields="user").execute()
        return about["user"]["emailAddress"]

    def create_folder(self, name: str, should_stop=None, log=None) -> str:
        """Create a folder in My Drive. Returns folder ID."""
        meta   = {"name": name, "mimeType": MIME_DIR}
        net_attempt = 0
        attempt = 0
        while attempt < 7:
            if should_stop and should_stop():
                raise RuntimeError("Đã dừng theo yêu cầu")
            try:
                folder = self._svc.files().create(body=meta, fields="id").execute()
                return folder["id"]
            except Exception as exc:
                # Chập mạng lúc tạo folder không được huỷ cả batch.
                if _is_network_error(exc) and net_attempt < len(NETWORK_RETRY_WAITS):
                    wait = NETWORK_RETRY_WAITS[net_attempt]
                    net_attempt += 1
                    _reset_connections(self._svc)
                    if log:
                        log(f"! Mất kết nối mạng tới Drive, thử tạo folder lại "
                            f"sau {wait:.0f}s ({net_attempt}/{len(NETWORK_RETRY_WAITS)})")
                    if not _wait_or_stop(wait, should_stop):
                        raise RuntimeError("Đã dừng theo yêu cầu")
                    continue
                attempt += 1
                if not _is_retryable_drive_error(exc) or attempt >= 7:
                    raise
                wait = 2.0   # chờ cố định 2s giữa các lần thử
                if log:
                    log(f"! Drive quá tải, thử tạo folder lại sau {wait:.1f}s "
                        f"({attempt}/7)")
                if not _wait_or_stop(wait, should_stop):
                    raise RuntimeError("Đã dừng theo yêu cầu")

        raise RuntimeError("Không thể tạo folder trên Drive")

    def upload_video(self, local_path: str, filename: str,
                     folder_id: str, progress_cb=None, should_stop=None,
                     log=None) -> str:
        """Upload video, make public, return shareable view link."""
        # Chunk 8MB: nếu đứt mạng chỉ phải gửi lại phần chunk dở, không phải
        # cả file (mặc định thư viện gửi 1 chunk 100MB).
        media = MediaFileUpload(local_path, mimetype=MIME_MP4, resumable=True,
                                chunksize=8 * 1024 * 1024)
        try:
            return self._upload_media(media, filename, folder_id,
                                      progress_cb, should_stop, log)
        finally:
            # MediaFileUpload chỉ đóng file trong __del__; còn mở thì Windows
            # không cho xoá file tạm (WinError 32) → rác dồn đầy ổ đĩa.
            try:
                media.stream().close()
            except Exception:
                pass

    def _upload_media(self, media, filename: str, folder_id: str,
                      progress_cb, should_stop, log) -> str:
        meta    = {"name": filename, "parents": [folder_id]}
        request = self._svc.files().create(
            body=meta, media_body=media, fields="id"
        )

        response = None
        # Each video has one retry budget shared by upload and permissions.
        max_retries = 3
        retry_attempt = 0
        # Lỗi mạng có ngân sách riêng (xem NETWORK_RETRY_WAITS).
        net_attempt = 0
        while response is None:
            if should_stop and should_stop():
                raise RuntimeError("Đã dừng theo yêu cầu")
            try:
                status, response = request.next_chunk()
            except Exception as exc:
                if _is_network_error(exc):
                    if net_attempt >= len(NETWORK_RETRY_WAITS):
                        raise
                    wait = NETWORK_RETRY_WAITS[net_attempt]
                    net_attempt += 1
                    _reset_connections(self._svc)
                    # Phiên resumable vẫn còn: đánh dấu lỗi để lần gọi tới hỏi
                    # Drive đã nhận bao nhiêu byte rồi upload tiếp từ đó,
                    # thay vì gửi lại từ đầu.
                    if getattr(request, "resumable_uri", None):
                        request._in_error_state = True
                    if log:
                        log(f"! Mất kết nối mạng tới Drive, "
                            f"upload tiếp sau {wait:.0f}s "
                            f"({net_attempt}/{len(NETWORK_RETRY_WAITS)})")
                    if not _wait_or_stop(wait, should_stop):
                        raise RuntimeError("Đã dừng theo yêu cầu")
                    continue
                if not _is_retryable_drive_error(exc) or retry_attempt >= max_retries:
                    raise
                wait = 1.0   # chờ cố định 1s giữa các lần thử
                retry_attempt += 1
                if log:
                    log(f"! Drive quá tải, thử upload lại sau {wait:.1f}s "
                        f"({retry_attempt}/{max_retries})")
                if not _wait_or_stop(wait, should_stop):
                    raise RuntimeError("Đã dừng theo yêu cầu")
                continue
            net_attempt = 0   # có tiến triển -> làm mới ngân sách lỗi mạng
            if status and progress_cb:
                progress_cb(status.progress())

        if progress_cb:
            progress_cb(1.0)

        file_id = response["id"]

        # Permission requests are also rate-limited.  Retry only documented
        # transient/rate errors with a fixed 1s delay.
        while True:
            if should_stop and should_stop():
                raise RuntimeError("Đã dừng theo yêu cầu")
            try:
                self._svc.permissions().create(
                    fileId=file_id,
                    body={"role": "reader", "type": "anyone"},
                ).execute()
                break
            except Exception as exc:
                if _is_network_error(exc) and net_attempt < len(NETWORK_RETRY_WAITS):
                    wait = NETWORK_RETRY_WAITS[net_attempt]
                    net_attempt += 1
                    _reset_connections(self._svc)
                    if log:
                        log(f"! Mất kết nối mạng tới Drive, "
                            f"cấp quyền lại sau {wait:.0f}s "
                            f"({net_attempt}/{len(NETWORK_RETRY_WAITS)})")
                    if not _wait_or_stop(wait, should_stop):
                        raise RuntimeError("Đã dừng theo yêu cầu")
                    continue
                if not _is_retryable_drive_error(exc) or retry_attempt >= max_retries:
                    raise
                wait = 1.0   # chờ cố định 1s giữa các lần thử
                retry_attempt += 1
                if log:
                    log(f"! Drive quá tải, thử cấp quyền lại sau {wait:.1f}s "
                        f"({retry_attempt}/{max_retries})")
                if not _wait_or_stop(wait, should_stop):
                    raise RuntimeError("Đã dừng theo yêu cầu")

        return f"https://drive.google.com/file/d/{file_id}/view"
