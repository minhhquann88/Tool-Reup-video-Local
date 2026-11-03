"""
edition.py — Cấu hình 2 bản ứng dụng: Pro và thường (khách).

Mọi khác biệt giữa 2 bản (app_id, tên, tiêu đề...) chỉ khai báo ở đây; code
khác đọc EDITION / PROFILE, không tự so sánh chuỗi app_id.

Chọn bản:
- Bản build: script build sinh `_edition_stamp.py` (EDITION = "khach") đóng
  cứng vào gói → bản đóng gói chỉ đọc file này, bỏ qua biến môi trường.
  Windows: `build.bat khach`; Linux: `EDITION=khach bash build_linux.sh`.
- Chạy mã nguồn: mặc định Pro; thử bản thường bằng REUP_EDITION=khach.

Module này KHÔNG import thư viện ngoài để script build / runtime hook / updater
dùng được trước khi cài dependencies.
"""

from __future__ import annotations   # cho phép 'X | None' chạy trên Python 3.8/3.9

import os
import sys

DEFAULT_EDITION = "pro"
ENV_VAR = "REUP_EDITION"
STAMP_FILE = "_edition_stamp.py"

# Giới hạn luồng: 2 bản chạy CÙNG một dây chuyền (App._run_batch), chỉ khác số.
#   user_can_set_workers : hiện ô "Số video cùng lúc" cho người dùng
#   max_workers          : trần số FFmpeg encode chạy đồng thời
#   default_workers      : số encode khi người dùng không chỉnh (hoặc không được chỉnh);
#                          có auto_workers thì đây là mức TỐI THIỂU
#   auto_workers         : mặc định tự tính theo số luồng CPU (xem default_workers_for)
#   default_delay        : giãn cách KHỞI ĐỘNG giữa 2 video (giây), mức tối thiểu
#                          bắt buộc; không hiện trên giao diện ở bản nào
#   prefetch / max_prefetch : số video Tải/Voice chạy trước khi tới lượt encode =
#                          số encode, kẹp trong [prefetch, max_prefetch] (xem prefetch_for)
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
        "auto_workers": True,
        "default_delay": 0.0,
        "prefetch": 2,
        "max_prefetch": 4,                    # trần: hạn chế lượt gọi AI/TTS cùng lúc
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
        "auto_workers": False,
        "default_delay": 20.0,                # 2 video bắt đầu cách nhau >= 20 giây
        "prefetch": 0,                        # xong hẳn 1 video mới sang video kế
        "max_prefetch": 0,
        "upload_concurrency": 1,
        "split_upload_pool": False,           # upload xong mới nhận video kế
        "single_instance": True,
    },
}


def default_workers_for(profile: dict, cpu_count: int | None = None) -> int:
    """Số video encode cùng lúc mặc định. auto_workers: ~1 encode / 4 luồng CPU
    (FFmpeg ultrafast mỗi tiến trình dùng tốt vài lõi), không dưới default_workers,
    không quá 6 và max_workers."""
    floor = profile["default_workers"]
    if not profile["auto_workers"]:
        return floor
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return max(floor, min(cpus // 4, 6, profile["max_workers"]))


def prefetch_for(profile: dict, workers: int) -> int:
    """Số video chuẩn bị trước: theo kịp số encode khi đặt nhiều video cùng lúc."""
    return max(profile["prefetch"], min(workers, profile["max_prefetch"]))


def _checked(name, source: str) -> str:
    value = str(name or "").strip().lower()
    if value not in PROFILES:
        raise ValueError(f"Bản ứng dụng không hợp lệ ({source}): {name!r}. "
                         f"Chọn một trong: {', '.join(PROFILES)}")
    return value


def _resolve() -> tuple:
    """Trả (bản, nguồn) — nguồn: "stamp" | "env" | "default"."""
    try:
        from _edition_stamp import EDITION as stamped    # type: ignore
    except ImportError:
        stamped = None
    if stamped is not None:
        return _checked(stamped, STAMP_FILE), "stamp"
    if not getattr(sys, "frozen", False):
        env = os.environ.get(ENV_VAR, "").strip()
        if env:
            return _checked(env, ENV_VAR), "env"
    return DEFAULT_EDITION, "default"


def write_stamp(directory, name: str):
    """Script build gọi: ghi `_edition_stamp.py` vào `directory`, trả đường dẫn."""
    from pathlib import Path
    path = Path(directory) / STAMP_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'EDITION = "{_checked(name, "tham số build")}"\n', encoding="utf-8")
    return path


def profile_for(name: str) -> dict:
    return PROFILES[_checked(name, "tham số")]


def edition_for_app_id(app_id: str) -> str | None:
    for name, values in PROFILES.items():
        if values["app_id"] == app_id:
            return name
    return None


def edition_for_filename(filename: str) -> str | None:
    """Bản của file build theo tên ("RenderVideoReupPro_v1.0.8.exe" → "pro").

    Tên gốc phải đứng đầu và theo sau là "_", "-", "." hoặc hết tên, nên
    "RenderVideoReup_…" không bị nhận nhầm thành bản Pro và ngược lại.
    Tên không theo mẫu (vd "setup.exe") → None.
    """
    for name, values in sorted(PROFILES.items(), key=lambda item: -len(item[1]["base_name"])):
        base = values["base_name"]
        if filename.startswith(base) and filename[len(base):][:1] in ("", "_", "-", "."):
            return name
    return None


EDITION, EDITION_SOURCE = _resolve()
PROFILE = PROFILES[EDITION]
APP_ID = PROFILE["app_id"]
BASE_NAME = PROFILE["base_name"]
TITLE = PROFILE["title"]
