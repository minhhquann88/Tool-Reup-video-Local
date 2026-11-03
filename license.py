"""
license.py — Khóa bản quyền cho Video Reup Tool.

Xác thực key với server key.byscom.vn (POST /api/validate-key) bằng bộ ba
key + app_id + deviceId. Logic thuần (không GUI) để dễ test; GUI nhập key nằm
ở main.py (LicenseDialog).

API LUÔN trả HTTP 200 — phải đọc trường `status` trong body JSON, không dựa
vào HTTP status code.
"""

from __future__ import annotations   # cho phép 'X | None' chạy trên Python 3.8/3.9

import sys
import uuid
from pathlib import Path

import requests
import app_paths
import edition

API_URL = "https://key.byscom.vn/api/validate-key"
APP_ID  = edition.APP_ID             # Pro/thường — khai báo trong edition.py
APP_VERSION = "1.0.8"              # SemVer gửi lên API; GUI thêm tiền tố v

_TIMEOUT = 15                        # giây — lỗi/timeout coi như không hợp lệ (chặt)
_NET_ERR = "Không thể kết nối máy chủ bản quyền."


# ── Dữ liệu cố định theo tài khoản hệ điều hành ─────────────────────────────
def _exe_dir() -> Path:
    """Compatibility helper for runtime logs; never the executable directory."""
    return app_paths.get_data_dir()


def get_license_path() -> Path:
    return app_paths.auth_path("license.json")


# ── deviceId: theo phần cứng (MachineGuid) → ổn định, không đổi khi xoá file ──
def get_device_id() -> str:
    """
    Trả mã định danh thiết bị ổn định. Ưu tiên MachineGuid của Windows; lỗi thì
    fallback theo địa chỉ MAC (uuid.getnode()).
    """
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
        guid = str(guid).strip().strip("{}").lower()
        if guid:
            return "win_" + guid
    except Exception:
        pass
    return "mac_" + format(uuid.getnode(), "x")


# ── Lưu / đọc / xóa key ───────────────────────────────────────────────────────
def load_saved_key() -> str | None:
    data, _ = app_paths.read_auth(get_license_path())
    return _key_value(data)


def _key_value(data) -> str | None:
    value = data.get("key") if data else None
    return value.strip() or None if isinstance(value, str) else None


def save_key(key: str, expire_date: str | None = None, *, expected_revision=None) -> bool:
    return app_paths.write_auth(get_license_path(), {"key": key, "expire_date": expire_date}, expected_revision)


def clear_key(*, expected_revision=None) -> bool:
    return app_paths.delete_auth(get_license_path(), expected_revision)


def _state_changed() -> dict:
    return {"is_valid": False, "data": None, "state_changed": True,
            "message": "Thông tin bản quyền đã thay đổi ở cửa sổ khác. Vui lòng thử lại."}


# ── Gọi API xác thực ──────────────────────────────────────────────────────────
def get_platform_name() -> str:
    if sys.platform == "win32":
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def validate_key(key: str) -> dict:
    """
    Xác thực key kèm phiên bản/nền tảng. Giữ trường update từ response.
    Lỗi mạng/timeout → {status:0, message:<lỗi kết nối>}.
    """
    try:
        res = requests.post(
            API_URL,
            json={
                "key": (key or "").strip(),
                "app_id": APP_ID,
                "deviceId": get_device_id(),   # camelCase — KHÔNG phải device_id
                "current_version": APP_VERSION,
                "platform": get_platform_name(),
            },
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        res.raise_for_status()
        result = res.json()
        if not isinstance(result, dict):
            raise ValueError("Phản hồi bản quyền không hợp lệ")
        return result                          # HTTP 200 vẫn phải xét status JSON
    except Exception:
        return {"status": 0, "message": _NET_ERR, "network_error": True}


# ── Quy trình kiểm tra đầy đủ ─────────────────────────────────────────────────
def check_license() -> dict:
    try:
        return _check_license()
    except app_paths.StorageError as exc:
        return {"is_valid": False, "data": None, "storage_error": True, "message": str(exc)}


def _check_license() -> dict:
    """
    Kiểm tra key đã lưu với server. Trả {is_valid, data, message}.
    Hợp lệ → lưu lại key + expire_date. Không hợp lệ → xoá key đã lưu.
    """
    snapshot, revision = app_paths.read_auth(get_license_path())
    saved = _key_value(snapshot)
    if not saved:
        return {"is_valid": False, "data": None,
                "message": "Chưa kích hoạt bản quyền."}

    result = validate_key(saved)
    if result.get("network_error"):
        # Mất mạng/timeout KHÔNG có nghĩa key sai → giữ key, để người dùng
        # thử lại khi có mạng thay vì phải nhập lại key.
        return {"is_valid": False, "data": None, "network_error": True,
                "message": result.get("message") or _NET_ERR}

    data = result.get("data")
    if result.get("status") == 1 and data and data.get("key"):
        if not save_key(saved, data.get("expire_date"), expected_revision=revision):
            return _state_changed()
        return {"is_valid": True, "data": data,
                "update": result.get("update"),
                "message": result.get("message", "Key hợp lệ")}

    try:
        if not clear_key(expected_revision=revision):
            return _state_changed()
    except app_paths.StorageError as exc:
        # Server has already rejected this key. A disk/permission error must
        # not turn that rejection into an allowed offline session.
        return {"is_valid": False, "data": None,
                "message": (result.get("message") or "Key không hợp lệ.") + "\n" + str(exc)}
    return {"is_valid": False, "data": None,
            "message": result.get("message") or "Key không hợp lệ."}


# ── Kiểm tra lại định kỳ (dùng cho recheck nền 30') ──────────────────────────
RECHECK_VALID   = "valid"
RECHECK_INVALID = "invalid"
RECHECK_NETERR  = "network_error"
RECHECK_CHANGED = "state_changed"
RECHECK_STORAGE = "storage_error"


def recheck_license() -> dict:
    """
    Kiểm tra lại key đã lưu — dùng cho kiểm tra định kỳ ngầm.
    Trả trạng thái RECHECK_* kèm data, message và update để GUI xử lý.

    QUAN TRỌNG: chỉ xoá key khi key thật sự không hợp lệ (INVALID).
    Lỗi mạng/timeout (NETERR) thì GIỮ NGUYÊN key để còn retry — tránh
    kích người dùng ra chỉ vì mạng chập chờn.
    """
    result = check_license()
    if result.get("state_changed"):
        status = RECHECK_CHANGED
    elif result.get("storage_error"):
        status = RECHECK_STORAGE
    elif result.get("network_error"):
        status = RECHECK_NETERR
    else:
        status = RECHECK_VALID if result["is_valid"] else RECHECK_INVALID
    return {"status": status, "data": result.get("data"),
            "update": result.get("update"), "message": result.get("message")}


def activate(key: str) -> dict:
    try:
        return _activate(key)
    except app_paths.StorageError as exc:
        return {"is_valid": False, "data": None, "storage_error": True, "message": str(exc)}


def _activate(key: str) -> dict:
    """
    Kích hoạt key người dùng nhập. Hợp lệ → lưu key + trả data; ngược lại trả lỗi.
    """
    key = (key or "").strip()
    if not key:
        return {"is_valid": False, "data": None, "message": "Vui lòng nhập key!"}

    _, revision = app_paths.read_auth(get_license_path())
    result = validate_key(key)
    data = result.get("data")
    if result.get("status") == 1 and data and data.get("key"):
        if not save_key(key, data.get("expire_date"), expected_revision=revision):
            return _state_changed()
        return {"is_valid": True, "data": data,
                "update": result.get("update"),
                "message": result.get("message", "Key hợp lệ")}

    return {"is_valid": False, "data": None,
            "message": result.get("message") or "Kích hoạt thất bại!"}
