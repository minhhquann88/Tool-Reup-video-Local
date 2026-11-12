"""
drive.py - Google Drive uploader using OAuth 2.0 (Desktop app credentials).

Login: the tool shows a Google login link; the user copies it into a browser on
this machine (the tool never opens a browser itself) → token.json is saved in
the per-user profile.
Subsequent runs: uses saved token automatically (refreshes if expired).
Multiple accounts: token.json is the ACTIVE account; drive_accounts.json keeps
every signed-in account so the user can switch without logging in again.

Files are uploaded to the authenticated user's own Drive → no quota issue.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import app_paths

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES   = ["https://www.googleapis.com/auth/drive"]
MIME_DIR = "application/vnd.google-apps.folder"
MIME_MP4 = "video/mp4"
ACCOUNTS_FILE = "drive_accounts.json"
NOT_LOGGED_IN = ("Chưa đăng nhập Google Drive. Bấm \"Thêm tài khoản\" ở phần "
                 "Nơi lưu video để đăng nhập.")

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
# tối đa 5 lần, chờ cố định 1s giữa các lần thử.
_NETWORK_ERRORS = (
    ssl.SSLError,            # gồm SSLEOFError, SSLZeroReturnError...
    ConnectionError,         # ConnectionReset/Aborted/Refused, BrokenPipe
    socket.timeout,
    TimeoutError,
    http.client.HTTPException,  # RemoteDisconnected, IncompleteRead...
)
NETWORK_RETRY_WAITS = (1.0,) * 5


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
    return app_paths.get_data_dir()


# Public path so main.py can reference it for logout
def get_token_path() -> Path:
    return app_paths.auth_path("token.json")


def clear_credentials():
    app_paths.delete_auth(get_token_path())


def _secret_path() -> Path:
    return _bundle_dir() / "client_secret.json"


def _check_login_cancelled(should_stop=None) -> None:
    if should_stop and should_stop():
        raise RuntimeError("Đã hủy đăng nhập Google Drive")


def _run_oauth_login(flow, on_auth_url, should_stop=None) -> Credentials:
    """Wait for the browser callback while allowing a new login to cancel us.

    Tool KHÔNG tự mở trình duyệt: ``on_auth_url(url)`` đưa link đăng nhập cho
    UI hiển thị, người dùng tự copy vào trình duyệt.  Link chỉ dùng được trên
    chính máy này (Google trả kết quả về localhost).
    """
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
        # select_account: luôn hiện màn chọn tài khoản (để thêm được tài khoản
        # khác dù trình duyệt đang đăng nhập sẵn); consent: Google luôn trả
        # refresh_token kể cả khi tài khoản đã cấp quyền trước đó.
        auth_url, _ = flow.authorization_url(prompt="select_account consent")
        _check_login_cancelled(should_stop)
        on_auth_url(auth_url)
        deadline = time.monotonic() + 600
        while not callback:
            _check_login_cancelled(should_stop)
            if time.monotonic() >= deadline:
                raise RuntimeError("Hết thời gian đăng nhập Google Drive. Hãy bấm Thêm tài khoản để thử lại.")
            server.handle_request()

    _check_login_cancelled(should_stop)
    # OAuthlib requires HTTPS when parsing the loopback authorization response.
    flow.fetch_token(authorization_response=callback[0].replace("http://", "https://", 1),
                     timeout=30)
    _check_login_cancelled(should_stop)
    return flow.credentials


def _require_secret() -> Path:
    secret = _secret_path()
    if not secret.exists():
        raise FileNotFoundError(
            f"Không tìm thấy:\n{secret}\n\n"
            "Đổi tên file client_secret_xxx.json thành client_secret.json\n"
            "và đặt cạnh file VideoReupTool.exe (hoặc main.py khi dev)."
        )
    return secret


def _get_credentials(should_stop=None, required=True) -> Credentials | None:
    """
    Return valid OAuth credentials of the active account (thread-safe).
    - token.json exists and valid  → reuse silently.
    - token.json expired           → refresh silently.
    - No token yet                 → raise (``required``) or return None.
      Đăng nhập chỉ qua nút "Thêm tài khoản" (add_account), không bao giờ tự
      bật trình duyệt giữa batch.
    """
    _require_secret()

    token = get_token_path()
    creds: Credentials | None = None

    revision = None

    def _read_token() -> Credentials | None:
        nonlocal revision
        data, revision = app_paths.read_auth(token, should_stop)
        if data is None:
            return None
        try:
            return Credentials.from_authorized_user_info(data, SCOPES)
        except Exception:
            # A prior crash can leave a truncated token.  Treat it as absent so
            # the user can authenticate again instead of blocking every worker.
            return None

    def _write_token(value: Credentials) -> None:
        if not app_paths.write_auth(token, json.loads(value.to_json()), revision, should_stop):
            raise RuntimeError("Thông tin đăng nhập Drive đã thay đổi hoặc đã đăng xuất ở cửa sổ khác. Vui lòng thử lại.")

    # Serialize token access; a cancelled caller stops waiting for the lock.
    _lock_creds(should_stop)
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
    finally:
        _CREDS_LOCK.release()
    if not required:
        return None
    raise RuntimeError(NOT_LOGGED_IN)


# ── Nhiều tài khoản ─────────────────────────────────────────────────────────
#
# token.json luôn là token của tài khoản ĐANG DÙNG (batch, làm mới token,
# đăng xuất… giữ nguyên như khi chỉ có 1 tài khoản).  drive_accounts.json lưu
# mọi tài khoản đã đăng nhập: {"schema": 1, "active": email,
# "accounts": {email: token}}.  Chuyển tài khoản = lưu lại token hiện tại vào
# danh sách rồi chép token của tài khoản được chọn vào token.json.

def get_accounts_path() -> Path:
    return app_paths.auth_path(ACCOUNTS_FILE)


def _normalize_store(data) -> dict:
    store = {"schema": 1, "active": "", "accounts": {}}
    if not isinstance(data, dict):
        return store
    accounts = data.get("accounts")
    if isinstance(accounts, dict):
        for email, token in accounts.items():
            if isinstance(email, str) and email.strip() and isinstance(token, dict):
                store["accounts"][email.strip()] = token
    if isinstance(data.get("active"), str):
        store["active"] = data["active"].strip()
    return store


def _read_store(should_stop=None):
    data, revision = app_paths.read_auth(get_accounts_path(), should_stop)
    return _normalize_store(data), revision


def _update_store(mutate, should_stop=None) -> dict:
    """Đọc–sửa–ghi danh sách tài khoản; ghi trùng với cửa sổ khác thì thử lại."""
    for _ in range(5):
        store, revision = _read_store(should_stop)
        mutate(store)
        if app_paths.write_auth(get_accounts_path(), store, revision, should_stop):
            return store
    raise RuntimeError("Danh sách tài khoản Drive đang bị thay đổi ở cửa sổ khác. Vui lòng thử lại.")


def _same_email(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def _put_account(store: dict, email: str, token: dict) -> None:
    """Ghi token của ``email`` mà GIỮ NGUYÊN vị trí trong danh sách.

    Email đã có (không phân biệt hoa/thường) → thay tại chỗ; chưa có → thêm
    cuối.  Nhờ vậy chọn tài khoản nào thì các dòng trong khung không nhảy chỗ.
    """
    accounts = store["accounts"]
    if not any(_same_email(key, email) for key in accounts):
        accounts[email] = token
        return
    store["accounts"] = {(email if _same_email(key, email) else key):
                         (token if _same_email(key, email) else value)
                         for key, value in accounts.items()}


def _fetch_email(creds: Credentials) -> str:
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    return svc.about().get(fields="user").execute()["user"]["emailAddress"]


def _lock_creds(should_stop=None) -> None:
    while not _CREDS_LOCK.acquire(timeout=0.2):
        _check_login_cancelled(should_stop)


def _activate(email: str, token_data: dict | None, should_stop=None) -> None:
    """Đặt ``email`` làm tài khoản đang dùng (cần giữ _CREDS_LOCK).

    ``token_data`` là token mới (vừa đăng nhập); None → lấy token đã lưu.
    Token của tài khoản đang dùng được lưu lại vào danh sách trước khi bị
    thay, để chuyển qua lại không phải đăng nhập lại.
    """
    current, revision = app_paths.read_auth(get_token_path(), should_stop)
    store, _ = _read_store(should_stop)
    old_email = store["active"]
    if current and not old_email:
        # Token có từ bản cũ (chưa biết email) → hỏi Google trước khi thay,
        # nếu không token đó sẽ mất.
        try:
            old_email = _fetch_email(Credentials.from_authorized_user_info(current, SCOPES))
        except Exception as exc:
            raise RuntimeError(
                "Không xác định được tài khoản Drive đang dùng để lưu lại. "
                "Kiểm tra mạng rồi thử lại, hoặc bấm Đăng xuất tài khoản này trước."
            ) from exc
    if token_data is None:
        token_data = next((tok for key, tok in store["accounts"].items()
                           if _same_email(key, email)), None)
        if token_data is None:
            raise RuntimeError(f"Không tìm thấy tài khoản Drive: {email}")

    def _save(s):
        if current and old_email:
            _put_account(s, old_email, current)
        _put_account(s, email, token_data)
    _update_store(_save, should_stop)

    _check_login_cancelled(should_stop)
    if not app_paths.write_auth(get_token_path(), token_data, revision, should_stop):
        raise RuntimeError("Thông tin đăng nhập Drive đã thay đổi hoặc đã đăng xuất ở cửa sổ khác. Vui lòng thử lại.")
    _update_store(lambda s: s.update(active=email), should_stop)


def list_accounts(should_stop=None) -> dict:
    """Danh sách tài khoản đã lưu (chỉ đọc file, không gọi mạng).

    ``active`` rỗng mà ``has_token`` True = token từ bản cũ, chưa rõ email.
    """
    store, _ = _read_store(should_stop)
    token, _ = app_paths.read_auth(get_token_path(), should_stop)
    active = store["active"] if token is not None else ""
    emails = list(store["accounts"])
    if active and active not in store["accounts"]:
        emails.append(active)
    return {"accounts": emails, "active": active, "has_token": token is not None}


def current_account_email(should_stop=None) -> str | None:
    """Email của tài khoản đang dùng (gọi Google); None nếu chưa đăng nhập.

    Đồng thời ghi email đó vào danh sách tài khoản (token từ bản cũ).
    """
    creds = _get_credentials(should_stop, required=False)
    if creds is None:
        return None
    email = _fetch_email(creds)
    _lock_creds(should_stop)
    try:
        current, _ = app_paths.read_auth(get_token_path(), should_stop)
        # Chỉ ghi khi token.json vẫn là token vừa hỏi (không bị đổi giữa chừng).
        if current and current.get("refresh_token") == creds.refresh_token:
            def _mark(s):
                _put_account(s, email, current)
                s["active"] = email
            _update_store(_mark, should_stop)
    finally:
        _CREDS_LOCK.release()
    return email


def add_account(on_auth_url, should_stop=None) -> str:
    """Đăng nhập thêm 1 tài khoản Google và chuyển sang dùng nó. Trả về email.

    ``on_auth_url(url)`` nhận link đăng nhập để UI hiển thị cho người dùng copy.
    """
    secret = _require_secret()
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    # Không giữ khóa trong lúc chờ trình duyệt: batch/làm mới token vẫn chạy.
    creds = _run_oauth_login(flow, on_auth_url, should_stop)
    _check_login_cancelled(should_stop)
    email = _fetch_email(creds)
    _check_login_cancelled(should_stop)
    token_data = json.loads(creds.to_json())
    _lock_creds(should_stop)
    try:
        _check_login_cancelled(should_stop)
        _activate(email, token_data, should_stop)
    finally:
        _CREDS_LOCK.release()
    return email


def switch_account(email: str, should_stop=None) -> None:
    """Chuyển sang tài khoản đã lưu (không cần đăng nhập lại)."""
    _lock_creds(should_stop)
    try:
        info = list_accounts(should_stop)
        if info["active"] and _same_email(info["active"], email):
            return
        _activate(email, None, should_stop)
    finally:
        _CREDS_LOCK.release()


def remove_account(email: str, should_stop=None) -> str:
    """Đăng xuất 1 tài khoản (``email`` rỗng = token chưa rõ email đang dùng).

    Xoá tài khoản đang dùng thì tự chuyển sang tài khoản còn lại đầu tiên.
    Trả về email đang dùng sau khi xoá ("" nếu không còn tài khoản nào).
    """
    _lock_creds(should_stop)
    try:
        info = list_accounts(should_stop)
        is_active = (not email) or (info["active"] and _same_email(info["active"], email))
        if not is_active:
            _update_store(lambda s: [s["accounts"].pop(k) for k in list(s["accounts"])
                                     if _same_email(k, email)], should_stop)
            return info["active"]

        clear_credentials()  # Cũng vô hiệu hoá lượt làm mới token đang dở.

        def _drop(s):
            for key in [k for k in s["accounts"] if email and _same_email(k, email)]:
                del s["accounts"][key]
            s["active"] = ""
        store = _update_store(_drop, should_stop)
        for other in list(store["accounts"]):
            try:
                _activate(other, None, should_stop)
                return other
            except Exception:
                continue
        return ""
    finally:
        _CREDS_LOCK.release()


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
        while attempt < 5:
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
                if not _is_retryable_drive_error(exc) or attempt >= 5:
                    raise
                wait = 1.0   # chờ cố định 1s giữa các lần thử
                if log:
                    log(f"! Drive quá tải, thử tạo folder lại sau {wait:.1f}s "
                        f"({attempt}/5)")
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
