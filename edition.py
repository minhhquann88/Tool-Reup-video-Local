"""
edition.py — Cấu hình 2 bản ứng dụng: Pro và thường (khách).

Mọi khác biệt giữa 2 bản (app_id, tên, tiêu đề...) chỉ khai báo ở đây; code
khác đọc EDITION / PROFILE, không tự so sánh chuỗi app_id.

Chọn bản:
- Bản build: script build sinh `_edition_stamp.py` (EDITION = "khach") đóng
  cứng vào gói → bản đóng gói chỉ đọc file này, bỏ qua biến môi trường.
- Chạy mã nguồn: mặc định Pro; thử bản thường bằng REUP_EDITION=khach.

Module này KHÔNG import thư viện ngoài để script build / runtime hook / updater
dùng được trước khi cài dependencies.
"""

from __future__ import annotations   # cho phép 'X | None' chạy trên Python 3.8/3.9

import os
import sys

DEFAULT_EDITION = "pro"
ENV_VAR = "REUP_EDITION"

PROFILES = {
    "pro": {
        "app_id": "tool_reup_video_pro",      # đã đăng ký trong bảng apps
        "base_name": "RenderVideoReupPro",    # tên file build + thư mục dữ liệu
        "title": "Render Video Reup Pro",
    },
    "khach": {
        "app_id": "tool_reup_video",
        "base_name": "RenderVideoReup",
        "title": "Render Video Reup",
    },
}


def _checked(name, source: str) -> str:
    value = str(name or "").strip().lower()
    if value not in PROFILES:
        raise ValueError(f"Bản ứng dụng không hợp lệ ({source}): {name!r}. "
                         f"Chọn một trong: {', '.join(PROFILES)}")
    return value


def _resolve() -> str:
    try:
        from _edition_stamp import EDITION as stamped    # type: ignore
    except ImportError:
        stamped = None
    if stamped is not None:
        return _checked(stamped, "_edition_stamp.py")
    if not getattr(sys, "frozen", False):
        env = os.environ.get(ENV_VAR, "").strip()
        if env:
            return _checked(env, ENV_VAR)
    return DEFAULT_EDITION


def profile_for(name: str) -> dict:
    return PROFILES[_checked(name, "tham số")]


def edition_for_app_id(app_id: str) -> str | None:
    for name, values in PROFILES.items():
        if values["app_id"] == app_id:
            return name
    return None


EDITION = _resolve()
PROFILE = PROFILES[EDITION]
IS_PRO = EDITION == "pro"
APP_ID = PROFILE["app_id"]
BASE_NAME = PROFILE["base_name"]
TITLE = PROFILE["title"]
