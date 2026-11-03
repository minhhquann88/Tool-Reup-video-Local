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

# Giới hạn luồng: 2 bản chạy CÙNG một dây chuyền (App._run_batch), chỉ khác số.
#   user_can_set_workers : hiện ô "Số video cùng lúc" / "Giãn cách" cho người dùng
#   max_workers          : trần số FFmpeg encode chạy đồng thời
#   default_workers      : số encode khi người dùng không chỉnh (hoặc không được chỉnh)
#   default_delay        : giãn cách KHỞI ĐỘNG giữa 2 video (giây); bản không cho
#                          chỉnh thì đây là mức tối thiểu bắt buộc
#   prefetch             : số video Tải/Voice chạy trước khi tới lượt encode
#   upload_concurrency   : số upload/cấp quyền Drive chạy đồng thời
#   split_upload_pool    : upload ở pool riêng (luồng xử lý đi làm video kế ngay)
#                          hay upload xong mới nhận video kế
#   single_instance      : chỉ cho mở 1 cửa sổ (tránh lách giới hạn bằng nhiều cửa sổ)
PROFILES = {
    "pro": {
        "app_id": "tool_reup_video_pro",      # đã đăng ký trong bảng apps
        "base_name": "RenderVideoReupPro",    # tên file build + thư mục dữ liệu
        "title": "Render Video Reup Pro",
        "user_can_set_workers": True,
        "max_workers": 8,
        "default_workers": 2,
        "default_delay": 0.0,
        "prefetch": 2,
        "upload_concurrency": 5,
        "split_upload_pool": True,
        "single_instance": False,
    },
    "khach": {
        "app_id": "tool_reup_video",
        "base_name": "RenderVideoReup",
        "title": "Render Video Reup",
        "user_can_set_workers": False,
        "max_workers": 1,
        "default_workers": 1,
        "default_delay": 30.0,
        "prefetch": 0,
        "upload_concurrency": 1,
        "split_upload_pool": False,
        "single_instance": True,
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
APP_ID = PROFILE["app_id"]
BASE_NAME = PROFILE["base_name"]
TITLE = PROFILE["title"]
