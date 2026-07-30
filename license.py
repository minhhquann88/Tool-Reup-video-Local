"""
license.py — Khóa bản quyền cho Video Reup Tool.

Xác thực key với server key.byscom.vn (POST /api/validate-key) bằng bộ ba
key + app_id + deviceId. Logic thuần (không GUI) để dễ test; GUI nhập key nằm
ở main.py (LicenseDialog).

API LUÔN trả HTTP 200 — phải đọc trường `status` trong body JSON, không dựa
vào HTTP status code.
"""

from __future__ import annotations   # cho phép 'X | None' chạy trên Python 3.8/3.9

import json
import os
import sys
import uuid
from pathlib import Path

import requests

API_URL = "https://key.byscom.vn/api/validate-key"
APP_ID  = "tool_reup_video_pro"      # đã đăng ký trong bảng apps (giữ đúng chữ thường)

_TIMEOUT = 15                        # giây — lỗi/timeout coi như không hợp lệ (chặt)
_NET_ERR = "Không thể kết nối máy chủ bản quyền."


# ── Nơi lưu file license (cạnh exe, giống token.json) ─────────────────────────
def _exe_dir() -> Path:
    """
    Thư mục GHI file runtime (license.json).
    - AppImage: mount chỉ-đọc → ghi cạnh file .AppImage (biến môi trường APPIMAGE);
      nếu thư mục đó không ghi được thì dùng ~/.config/VideoReupTool.
    - Frozen khác (exe Windows / onedir): cạnh file thực thi.
    - Dev: cạnh file nguồn.
    """
    appimg = os.environ.get("APPIMAGE")
    if appimg:
        here = Path(appimg).parent
        if os.access(str(here), os.W_OK):
            return here
        _cfg_name = "RenderVideoReupPro" if APP_ID == "tool_reup_video_pro" else "RenderVideoReup"
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / _cfg_name
        try:
            cfg.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return cfg
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def get_license_path() -> Path:
    return _exe_dir() / "license.json"


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
    try:
        data = json.loads(get_license_path().read_text(encoding="utf-8"))
        return (data.get("key") or "").strip() or None
    except Exception:
        return None


def save_key(key: str, expire_date: str | None = None) -> None:
    try:
        get_license_path().write_text(
            json.dumps({"key": key, "expire_date": expire_date},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def clear_key() -> None:
    try:
        p = get_license_path()
        if p.exists():
            p.unlink()
    except OSError:
        pass


# ── Gọi API xác thực ──────────────────────────────────────────────────────────
def validate_key(key: str) -> dict:
    """
    Gửi key + app_id + deviceId lên server. Trả về dict {status, message, data?}.
    Lỗi mạng/timeout → {status:0, message:<lỗi kết nối>}.
    """
    try:
        res = requests.post(
            API_URL,
            json={
                "key": (key or "").strip(),
                "app_id": APP_ID,
                "deviceId": get_device_id(),   # camelCase — KHÔNG phải device_id
            },
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        return res.json()                       # API luôn 200 → đọc thẳng JSON
    except Exception:
        return {"status": 0, "message": _NET_ERR, "network_error": True}


# ── Quy trình kiểm tra đầy đủ ─────────────────────────────────────────────────
def check_license() -> dict:
    """
    Kiểm tra key đã lưu với server. Trả {is_valid, data, message}.
    Hợp lệ → lưu lại key + expire_date. Không hợp lệ → xoá key đã lưu.
    """
    saved = load_saved_key()
    if not saved:
        return {"is_valid": False, "data": None,
                "message": "Chưa kích hoạt bản quyền."}

    result = validate_key(saved)
    data = result.get("data")
    if result.get("status") == 1 and data and data.get("key"):
        save_key(saved, data.get("expire_date"))
        return {"is_valid": True, "data": data,
                "message": result.get("message", "Key hợp lệ")}

    clear_key()
    return {"is_valid": False, "data": None,
            "message": result.get("message") or "Key không hợp lệ."}


# ── Kiểm tra lại định kỳ (dùng cho recheck nền 30') ──────────────────────────
RECHECK_VALID   = "valid"
RECHECK_INVALID = "invalid"
RECHECK_NETERR  = "network_error"


def recheck_license() -> str:
    """
    Kiểm tra lại key đã lưu — dùng cho kiểm tra định kỳ ngầm.
    Trả về RECHECK_VALID / RECHECK_INVALID / RECHECK_NETERR.

    QUAN TRỌNG: chỉ xoá key khi key thật sự không hợp lệ (INVALID).
    Lỗi mạng/timeout (NETERR) thì GIỮ NGUYÊN key để còn retry — tránh
    kích người dùng ra chỉ vì mạng chập chờn.
    """
    saved = load_saved_key()
    if not saved:
        return RECHECK_INVALID

    result = validate_key(saved)

    if result.get("network_error"):
        return RECHECK_NETERR                       # mất mạng → giữ key, retry

    data = result.get("data")
    if result.get("status") == 1 and data and data.get("key"):
        save_key(saved, data.get("expire_date"))    # gia hạn expire_date
        return RECHECK_VALID

    clear_key()                                     # key bị hủy/hết hạn → xoá
    return RECHECK_INVALID


def activate(key: str) -> dict:
    """
    Kích hoạt key người dùng nhập. Hợp lệ → lưu key + trả data; ngược lại trả lỗi.
    """
    key = (key or "").strip()
    if not key:
        return {"is_valid": False, "data": None, "message": "Vui lòng nhập key!"}

    result = validate_key(key)
    data = result.get("data")
    if result.get("status") == 1 and data and data.get("key"):
        save_key(key, data.get("expire_date"))
        return {"is_valid": True, "data": data,
                "message": result.get("message", "Key hợp lệ")}

    return {"is_valid": False, "data": None,
            "message": result.get("message") or "Kích hoạt thất bại!"}
