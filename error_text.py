"""
Dịch exception thành thông báo tiếng Việt ngắn gọn cho người dùng.

Mỗi lỗi được quy về: mã lỗi (để báo dev), nguyên nhân dễ hiểu, cách xử lý và
một dòng chi tiết kỹ thuật ngắn.  Traceback đầy đủ vẫn được ghi vào
app_error.log — module này chỉ lo phần hiển thị.
"""
from __future__ import annotations

import errno
import http.client
import re
import socket
import ssl
from dataclasses import dataclass

REPORT_HINT = "Nếu lặp lại, gửi mã lỗi + file app_error.log cho dev."


@dataclass
class ErrorInfo:
    code: str      # ví dụ "NET-DRIVE"
    cause: str     # nguyên nhân, 1 câu
    fix: str       # cách xử lý, 1 câu
    detail: str    # chi tiết kỹ thuật rút gọn (1 dòng)

    def short(self) -> str:
        """Dạng 1 dòng cho log thử lại: 'Nguyên nhân [MÃ]'."""
        return f"{self.cause} [{self.code}]"

    def lines(self) -> list[str]:
        return [
            f"   • Lỗi: {self.cause} [{self.code}]",
            f"   • Cách xử lý: {self.fix}",
            f"   • Chi tiết cho dev: {self.detail}",
        ]


# ── Thu thập thông tin từ chuỗi exception ────────────────────────────────────

def _chain(exc: BaseException) -> list[BaseException]:
    """exc và các nguyên nhân gốc, ngoài → trong.

    Chỉ đi theo __cause__ (raise ... from ...).  __context__ là lỗi khác đã
    xảy ra trước đó trong khối except, thường không liên quan và làm sai
    phân loại (ví dụ một lần "Đã dừng" cũ dính vào lỗi TTS thật).
    """
    out: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and cur not in out and len(out) < 10:
        out.append(cur)
        cur = cur.__cause__
    return out


def _tb_files(chain: list[BaseException]) -> str:
    names = []
    for e in chain:
        tb = e.__traceback__
        while tb is not None:
            names.append(tb.tb_frame.f_code.co_filename.replace("\\", "/").lower())
            tb = tb.tb_next
    return " ".join(names)


def _status_code(chain: list[BaseException], text: str) -> int | None:
    for e in chain:
        for holder in (getattr(e, "resp", None), getattr(e, "response", None)):
            code = getattr(holder, "status", None) or getattr(holder, "status_code", None)
            if isinstance(code, int):
                return code
    m = re.search(r"\bhttp (\d{3})\b", text) or re.search(r"\b(\d{3}) (?:client|server) error\b", text)
    return int(m.group(1)) if m else None


def _is_network(e: BaseException) -> bool:
    if isinstance(e, (ssl.SSLError, ConnectionError, socket.timeout, TimeoutError,
                      http.client.HTTPException, socket.gaierror)):
        return True
    try:
        import requests
        # ChunkedEncodingError: đứt kết nối giữa lúc đang tải (IncompleteRead).
        if isinstance(e, (requests.ConnectionError, requests.Timeout,
                          requests.exceptions.ChunkedEncodingError,
                          requests.exceptions.ContentDecodingError)):
            return True
    except ImportError:  # pragma: no cover
        pass
    try:
        import httplib2
        if isinstance(e, httplib2.ServerNotFoundError):
            return True
    except ImportError:  # pragma: no cover
        pass
    return False


def _detail(chain: list[BaseException]) -> str:
    """Lỗi gốc trong cùng: 'TênLỗi: dòng đầu thông báo' (cắt ngắn)."""
    root = chain[-1]
    msg = next((ln.strip() for ln in str(root).splitlines() if ln.strip()), "")
    text = f"{type(root).__name__}: {msg}" if msg else type(root).__name__
    return text if len(text) <= 160 else text[:157] + "…"


_STAGES = {"DRIVE": "upload lên Google Drive", "AI": "tạo lời thoại AI",
           "VOICE": "tạo giọng đọc", "FFMPEG": "xử lý video", "DL": "tải video"}


def _stage(text: str, files: str) -> tuple[str, str]:
    """(mã giai đoạn, mô tả) — lỗi xảy ra ở bước nào.

    Chỉ dò theo tiền tố thông báo do tool tự đặt, KHÔNG theo từ lẻ như
    "audio"/"drive": stderr FFmpeg luôn có "Audio: aac", và link nguồn
    drive.usercontent.google.com chứa "drive" — dò từ lẻ sẽ gán nhầm bước.
    """
    if "client_secret" in text or "đăng nhập google drive" in text:
        return "DRIVE", "đăng nhập Google Drive"
    if text.startswith(("ffmpeg", "không đọc được thời lượng video")):
        return "FFMPEG", "xử lý video"
    if (text.startswith(("tải video thất bại", "url không trả về video",
                         "link trích từ trang", "link video đã hết hạn"))
            or "file tải về" in text or "tải thiếu dữ liệu" in text):
        return "DL", "tải video"
    if any(k in text for k in ("gemini", "chatgpt", "openai", "lời thoại")):
        return "AI", "tạo lời thoại AI"
    if any(k in text for k in ("google tts", "autovoice", "voice api",
                               "file audio", "audio quá")):
        return "VOICE", "tạo giọng đọc"
    if ("drive.py" in files or "googleapiclient" in files or "httplib2" in files
            or "google drive" in text or "trên drive" in text):
        return "DRIVE", "upload lên Google Drive"
    if "ffprobe" in text:
        return "FFMPEG", "xử lý video"
    if ("tải" in text or "content-type" in text
            or "requests" in files or "urllib3" in files):
        return "DL", "tải video"
    return "APP", "xử lý"


# ── Phân loại ────────────────────────────────────────────────────────────────

def explain(exc: BaseException, stage: str | None = None) -> ErrorInfo:
    """stage: gợi ý bước đang chạy ("DL", "AI", "VOICE", "FFMPEG", "DRIVE")."""
    chain = _chain(exc)
    text = " | ".join(str(e) for e in chain).lower()
    files = _tb_files(chain)
    if stage in _STAGES:
        stage, where = stage, _STAGES[stage]
    else:
        stage, where = _stage(text, files)
    detail = _detail(chain)

    def info(code, cause, fix):
        return ErrorInfo(code, cause, fix, detail)

    outer = str(exc).lower()
    if "đã dừng theo yêu cầu" in outer or "đã hủy đăng nhập" in outer:
        return info("STOP", "Đã dừng theo yêu cầu người dùng", "Không cần xử lý.")

    # Cấu hình / dữ liệu đầu vào
    if "client_secret" in text:
        return info("CFG-SECRET", "Thiếu file client_secret.json",
                    "Đặt file client_secret.json cạnh file chạy của tool rồi mở lại.")
    if "thiếu api key" in text:
        return info(f"CFG-KEY-{stage}", "Chưa nhập API key",
                    "Nhập API key trong phần cài đặt rồi chạy lại.")
    if "product_name" in text:
        return info("DATA-NAME", "Ô product_name trong file CSV bị trống",
                    "Điền tên sản phẩm cho dòng này trong CSV rồi chạy lại.")
    if "trim quá dài" in text:
        return info("CFG-TRIM", "Thời gian cắt đầu/cuối dài hơn cả video",
                    "Giảm số giây cắt đầu/cuối trong cài đặt.")
    if "file excel trống" in text:
        return info("DATA-EMPTY", "File dữ liệu không có dòng nào",
                    "Kiểm tra lại file CSV/Excel đã chọn.")

    if "link video đã hết hạn" in text:
        return info("DL-EXPIRED", "Link video trong CSV đã hết hạn",
                    "Xuất lại file CSV để có link video mới rồi chạy lại.")

    # Ổ đĩa / quyền file
    for e in chain:
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            return info("DISK-FULL", "Ổ đĩa đã hết dung lượng",
                        "Giải phóng dung lượng ổ đĩa rồi chạy lại.")
        if isinstance(e, PermissionError):
            return info("FILE-PERM",
                        "Không ghi/đọc được file (file đang mở ở chương trình khác hoặc thiếu quyền)",
                        "Đóng file đang mở (Excel...), chọn thư mục khác rồi chạy lại.")

    # Đăng nhập Drive hết hạn
    if any(type(e).__name__ == "RefreshError" for e in chain):
        return info("DRIVE-AUTH", "Phiên đăng nhập Google Drive đã hết hạn",
                    "Bấm Đăng nhập Google Drive lại rồi chạy lại.")

    # Lỗi HTTP từ máy chủ
    status = _status_code(chain, text)
    if status:
        service = {"DRIVE": "Google Drive", "AI": "AI tạo lời thoại",
                   "VOICE": "dịch vụ giọng đọc", "DL": "máy chủ video"}.get(stage, "máy chủ")
        if status == 429 or (status == 403 and ("ratelimitexceeded" in text or "quota" in text)):
            if "storagequotaexceeded" in text:
                return info("DRIVE-FULL", "Google Drive đã hết dung lượng",
                            "Dọn bớt Drive hoặc đăng nhập tài khoản khác.")
            return info(f"LIMIT-{stage}", f"{service} đang giới hạn số lượt gọi (quá nhiều yêu cầu / hết quota)",
                        "Chờ vài phút, giảm số luồng chạy, hoặc kiểm tra quota tài khoản.")
        if stage == "DL" and status in (403, 404, 410):
            return info(f"DL-{status}", "Link video đã hết hạn, bị xóa hoặc bị chặn",
                        "Lấy link video mới cho dòng này trong CSV rồi chạy lại.")
        if stage == "DRIVE" and status == 401:
            return info("DRIVE-AUTH", "Phiên đăng nhập Google Drive đã hết hạn",
                        "Bấm Đăng nhập Google Drive lại rồi chạy lại.")
        if stage == "DRIVE" and status == 404:
            return info("DRIVE-404", "Không tìm thấy thư mục trên Google Drive (có thể đã bị xóa)",
                        "Chạy lại để tool tạo thư mục mới.")
        if status in (401, 403):
            return info(f"KEY-{stage}", f"API key của {service} sai, hết hạn hoặc chưa được cấp quyền",
                        "Kiểm tra lại API key / tài khoản trong phần cài đặt.")
        if status >= 500:
            return info(f"SERVER-{stage}", f"Máy chủ {service} đang lỗi tạm thời (HTTP {status})",
                        "Chờ vài phút rồi chạy lại video lỗi.")
        if 400 <= status < 500:
            return info(f"HTTP{status}-{stage}", f"{service} từ chối yêu cầu (HTTP {status})",
                        REPORT_HINT)

    # Mạng
    if any(_is_network(e) for e in chain) or "kết nối bị đứt" in text:
        return info(f"NET-{stage}", f"Mất kết nối mạng khi {where}",
                    "Kiểm tra Internet / VPN / phần mềm diệt virus rồi chạy lại video lỗi.")

    # Tải video
    if stage == "DL" and any(k in text for k in ("không phải video", "không trả về video",
                                                 "quá nhỏ", "tải video thất bại")):
        return info("DL-BAD", "Link không tải được video (link sai, hết hạn hoặc bị chặn)",
                    "Mở thử link trên trình duyệt; nếu hỏng, thay link mới trong CSV.")

    # AI / giọng đọc
    if "lời thoại quá dài" in text:
        return info("AI-LONG", "AI tạo lời thoại dài hơn giới hạn cho phép",
                    "Sửa prompt yêu cầu ngắn hơn hoặc tăng giới hạn ký tự.")
    if stage == "AI" and ("rỗng" in text or "thất bại" in text):
        return info("AI-EMPTY", "AI không trả về lời thoại", "Chạy lại; " + REPORT_HINT)
    if stage == "VOICE" and any(k in text for k in ("rỗng", "quá ngắn", "quá nhỏ",
                                                    "không có audio", "không tồn tại")):
        return info("VOICE-BAD", "Dịch vụ giọng đọc trả về audio lỗi hoặc rỗng",
                    "Chạy lại; nếu vẫn lỗi, thử giọng đọc/dịch vụ khác.")

    # FFmpeg
    if "quá thời hạn" in text:
        return info("FFMPEG-TIMEOUT", "Xử lý video quá lâu (quá 10 phút)",
                    "Giảm số luồng chạy cùng lúc; kiểm tra máy có đang quá tải không.")
    if stage == "FFMPEG":
        return info("FFMPEG-ERR", "Không xử lý được video này (file nguồn hỏng hoặc cài đặt không hợp lệ)",
                    "Thử video khác để kiểm tra; " + REPORT_HINT)

    return info(f"UNKNOWN-{stage}", f"Lỗi chưa xác định khi {where}", REPORT_HINT)


def short_reason(exc: BaseException, stage: str | None = None) -> str:
    """1 dòng ngắn cho log 'thử lại lần x/y'."""
    return explain(exc, stage).short()
