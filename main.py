"""
main.py - Render Video Reup Pro
Flow: CSV → download temp → FFmpeg → lưu (upload Drive | move vào thư mục local)
      → output CSV (xoá cột video_url, thay bằng cột 'Link Video' = link Drive/local path, lỗi = "Lỗi", chưa chạy = rỗng)
"""

from __future__ import annotations   # cho phép 'X | None' chạy trên Python 3.8/3.9

import csv
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

# ── Fix X11 BadLength (RenderAddGlyphs) — Layer 1: env vars trước khi import Tk ───────
# Ép Xft dùng 1-bit monochrome glyphs (giảm 97% kích thước bitmap) & 24-bit visuals
if sys.platform != "win32":
    # Fontconfig is initialized by Tk/Xft during the first font creation, so
    # this must point at our policy before tkinter/customtkinter is imported.
    _fontconfig_base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    _fontconfig_candidates = (
        _fontconfig_base / "linux-fontconfig.conf",
        Path(__file__).parent / "linux-fontconfig.conf",
    )
    if "FONTCONFIG_FILE" not in os.environ:
        for _fontconfig_file in _fontconfig_candidates:
            if _fontconfig_file.is_file():
                os.environ["FONTCONFIG_FILE"] = str(_fontconfig_file)
                break
    os.environ["XFT_ANTIALIAS"] = "0"
    os.environ["XFT_HINTING"] = "0"
    os.environ["XFT_RGBA"] = "none"
    os.environ["XFT_MAX_GLYPH_MEMORY"] = "10485760"
    os.environ["XLIB_SKIP_ARGB_VISUALS"] = "1"
    os.environ["GDK_SCALE"] = "1"
    os.environ["GDK_DPI_SCALE"] = "1"
    os.environ.pop("WAYLAND_DISPLAY", None)
# ────────────────────────────────────────────────────────────────────────────

from tkinter import BooleanVar, StringVar, TclError, filedialog, messagebox

import customtkinter as ctk

import edition
import hwinfo
import license as license_mod
import single_instance
import updater
from urllib.parse import urlparse

# CTk normally draws rounded corners/checkmarks with a private OTF glyph font.
# On X11 that creates an avoidable RenderAddGlyphs path, so use polygons instead.
if sys.platform != "win32":
    try:
        from customtkinter.windows.widgets.core_rendering.draw_engine import DrawEngine
        DrawEngine.preferred_drawing_method = "polygon_shapes"
    except Exception:
        pass


def _patch_ctk_scroll_for_tcl_widgets() -> None:
    """CTkScrollableFrame gắn bind_all cho con lăn chuột rồi đi ngược widget.master.
    Hộp chọn file / messagebox của Tk trên Linux là widget thuần Tcl → event.widget
    là chuỗi (vd '.__tk_filedialog.contents.f2.list') → AttributeError 'str' ... 'master'
    mỗi lần lăn chuột trong hộp thoại. Widget không phải của Python thì bỏ qua."""
    try:
        from customtkinter import CTkScrollableFrame
    except Exception:
        return
    orig = getattr(CTkScrollableFrame, "_check_if_valid_scroll", None)
    if orig is None or getattr(orig, "_reup_safe", False):
        return

    def _safe_check(self, widget):
        if not hasattr(widget, "master"):
            return False
        try:
            return orig(self, widget)
        except (AttributeError, TclError):
            return False

    _safe_check._reup_safe = True
    CTkScrollableFrame._check_if_valid_scroll = _safe_check


_patch_ctk_scroll_for_tcl_widgets()


def _file_types(*groups):
    """filetypes cho filedialog. Hộp chọn file Tk trên Linux phân biệt hoa/thường
    (ẩn 'NHAC.MP3') và '*.*' bỏ sót file không có đuôi → thêm mẫu HOA, dùng '*'."""
    out = []
    for label, patterns in groups:
        pats = patterns.split()
        if sys.platform != "win32":
            pats += [p.upper() for p in pats if p.upper() != p]
        out.append((label, " ".join(pats)))
    out.append(("Tất cả", "*.*" if sys.platform == "win32" else "*"))
    return out

APP_VERSION = f"v{license_mod.APP_VERSION}"
PROFILE = edition.PROFILE           # giới hạn luồng + định danh theo bản Pro/thường
APP_TITLE = f"{edition.TITLE} {APP_VERSION}"
_UI_FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
_UI_MONO_FONT = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"

# Cột cài đặt bên trái: rộng, chữ to, chia tab để ít phải cuộn.
_SETTINGS_TABS = ("Video", "Logo", "Âm thanh", "Lưu video")
_SET_W     = 400                       # chiều rộng cột cài đặt (px)
_PAUSE_TEXT,  _PAUSE_COLOR  = "TẠM DỪNG", "#B26A00"
_RESUME_TEXT, _RESUME_COLOR = "TIẾP TỤC", "#2E7D32"
_SET_WRAP  = _SET_W - 70               # wraplength cho chữ nhiều dòng
_F_SET     = (_UI_FONT, 14)            # chữ thường
_F_HINT    = (_UI_FONT, 12)            # chữ gợi ý (xám)
_F_SECTION = (_UI_FONT, 16, "bold")    # tiêu đề nhóm
_ROW_H     = 34                        # chiều cao ô nhập / nút / menu
_CTRL_W    = 200                       # độ rộng menu chọn / ô nhập bên phải

def _clean_ui_text(text: str, max_len: int = 60) -> str:
    """Sanitize text for display in UI widgets to prevent X11 BadLength (RenderAddGlyphs) crash on Linux."""
    if not text:
        return ""
    s = str(text).strip()
    if sys.platform != "win32":
        s = "".join(c for c in s if ord(c) <= 0xFFFF)
    if len(s) > max_len:
        s = s[:max_len-3] + "..."
    return s


# ── Fix X11 BadLength — Layer 2: tắt CTk DPI auto-detect trước khi tạo bất kỳ cửa sổ nào ──
# CTk 5.2+ có _check_dpi_scaling chạy định kỳ và override tk.scaling → gây crash.
if sys.platform != "win32":
    try:
        ctk.deactivate_automatic_dpi_awareness()   # CTk >= 5.2.0
    except AttributeError:
        pass
    try:
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
    except Exception:
        pass
# ─────────────────────────────────────────────────────────────────────────────

from core import FFMPEG, VideoDownloader, VideoProcessor, link_expiry
from error_text import explain as explain_error
from tts import (DEFAULT_PROMPT, DEFAULT_AUTOVOICE_VOICE, DEFAULT_VIDEOAI_VOICE,
                 DEFAULT_VOICE_LABEL, VOICE_CHOICES, make_voice)

# ── Theme ────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
if sys.platform != "win32":
    try:
        ctk.ThemeManager.theme["CTkFont"]["family"] = _UI_FONT
    except (AttributeError, KeyError, TypeError):
        pass

_WIN_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _apply_linux_x11_fix(tk_root) -> None:
    """Áp dụng workaround triệt để cho lỗi X11 BadLength - RenderAddGlyphs trên Linux.

    Fix bao gồm các bước khi mỗi cửa sổ CTk được tạo:
    1. Tắt CTk DPI auto-detect & ép widget/window scaling = 1.0.
    2. Ép Tk internal scaling = 1.0 → glyph nhỏ hơn → request X11 không vượt giới hạn (64KB).
    3. Tự động phát hiện font hệ thống có sẵn (Ubuntu, Liberation Sans, FreeSans, Arial) → fallback an toàn.
    """
    if sys.platform == "win32":
        return
    try:
        try:
            ctk.deactivate_automatic_dpi_awareness()
        except AttributeError:
            pass
        try:
            ctk.set_widget_scaling(1.0)
            ctk.set_window_scaling(1.0)
        except Exception:
            pass
        try:
            tk_root.tk.call("tk", "scaling", 1.0)
            tk_root.tk.call("tk", "scaling", "-displayof", ".", 1.0)
        except Exception:
            pass
        import tkinter.font as tkfont
        try:
            avail = set(tkfont.families(tk_root))
            chosen = "sans-serif"
            for f_cand in ("Ubuntu", "Liberation Sans", "DejaVu Sans", "FreeSans", "Arial", "sans-serif"):
                if f_cand in avail:
                    chosen = f_cand
                    break
            for fn in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkTooltipFont"):
                try:
                    tkfont.nametofont(fn).configure(family=chosen, size=10)
                except Exception:
                    pass
            try:
                ctk.FontManager._font_family = chosen
            except Exception:
                pass
        except Exception:
            pass
        # Vô hiệu hoá hoàn toàn vòng lặp kiểm tra DPI định kỳ của CTk
        # (belt-and-suspenders thêm vào deactivate_automatic_dpi_awareness).
        # Nếu _check_dpi_scaling chạy lại sẽ override tk.scaling=1.0 → X11 BadLength crash.
        try:
            ctk.CTk._check_dpi_scaling = lambda self: None
        except Exception:
            pass
    except Exception:
        pass


# ── Cấu hình kiểm tra license định kỳ (ép thời gian ngắn lại khi test) ────────
_RECHECK_SECONDS = int(os.environ.get("LICENSE_RECHECK_SECONDS", 30 * 60))  # 30'
_RETRY_SECONDS   = int(os.environ.get("LICENSE_RETRY_SECONDS",  300))       # 5'
_MAX_RETRIES     = int(os.environ.get("LICENSE_MAX_RETRIES",     3))        # 3 lần


def _resource(name: str) -> Path:
    """Locate a bundled resource (works in dev and inside a PyInstaller exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def _crash_dir() -> Path:
    """Thư mục GHI ĐƯỢC để lưu crash.log (AppImage-aware qua license_mod)."""
    try:
        return license_mod._exe_dir()
    except Exception:
        return Path(__file__).parent


def _write_crash(tb: str) -> None:
    """Ghi traceback ra crash.log để chẩn đoán khi chạy không có console."""
    try:
        path = _crash_dir() / "crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as crash_file:
            crash_file.write(f"\n[{timestamp}]\n{str(tb).rstrip()}\n")
    except Exception:
        pass

# ── Phân loại log ─────────────────────────────────────────────────────────────
# Mỗi dòng log gắn 1 nhãn để lọc bằng dropdown. Dòng hệ thống (cat=None) chỉ
# xuất hiện ở mục "Tất cả".
LOG_DOWNLOAD = "Tải video"
LOG_TEXT     = "Tạo text"
LOG_VOICE    = "Tạo voice"
LOG_PROCESS  = "Xử lý video"
LOG_UPLOAD   = "Upload"
LOG_RESULT   = "Kết quả"
LOG_ERROR    = "Lỗi"
LOG_CATS        = (LOG_DOWNLOAD, LOG_TEXT, LOG_VOICE, LOG_PROCESS, LOG_UPLOAD, LOG_RESULT, LOG_ERROR)
LOG_FILTER_ALL  = "Tất cả"
LOG_FILTER_OPTS = (LOG_FILTER_ALL,) + LOG_CATS

# Keep the latest 1,000 non-error UI log lines and all errors.
MAX_UI_LOGS = 1000
_ERROR_LOG_LOCK = threading.Lock()

# Mục trong menu chọn tài khoản Drive khi chưa có / chưa rõ email.
_NO_DRIVE_ACCOUNT = "Chưa có tài khoản"
_UNKNOWN_DRIVE_ACCOUNT = "Tài khoản hiện tại (chưa rõ email)"
_RETRY_LOG_RE = re.compile(
    r"^(?P<prefix>.*?(?:tải\s+lần|thử\s+lại)\s+)"
    r"(?P<attempt>\d+)\s*/\s*(?P<total>\d+)(?P<suffix>.*)$",
    re.IGNORECASE,
)


# ── Hiển thị danh sách (phân trang) ───────────────────────────────────────────
# Chỉ render mỗi trang _PAGE_SIZE dòng để tránh treo khi CSV có hàng nghìn video.
_PAGE_SIZE = 20

# ── VideoRow ─────────────────────────────────────────────────────────────────

class VideoRow(ctk.CTkFrame):
    """Compact row showing only a selection checkbox and product name."""

    _ROW_H = 32

    def __init__(self, parent, app, video_data: dict, global_index: int, **kw):
        bg = "#2a2a2a" if global_index % 2 == 0 else "#313131"
        super().__init__(parent, fg_color=bg, corner_radius=4,
                         height=self._ROW_H, **kw)
        self.pack_propagate(False)

        self._app         = app
        self.global_index = global_index
        self.video_data   = video_data
        # Lựa chọn lấy từ model (giữ nguyên khi chuyển trang qua lại)
        self.selected     = BooleanVar(value=app._selected[global_index])

        self.chk = ctk.CTkCheckBox(
            self, text="", variable=self.selected, command=self._on_toggle,
            width=28, checkbox_width=18, checkbox_height=18,
        )
        self.chk.pack(side="left", padx=(8, 2))

        val = video_data.get("product_name") if video_data.get("product_name") is not None else video_data.get("productName")
        p_name = "" if val is None or str(val).strip().lower() in ("", "nan", "none") else str(val).strip()
        name = p_name if p_name else "(dữ liệu product_name rỗng)"
        disp_name = _clean_ui_text(name, max_len=60)
        self.name_lbl = ctk.CTkLabel(
            self, text=disp_name, anchor="w",
            font=(_UI_FONT, 12), justify="left",
        )
        self.name_lbl.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def _on_toggle(self):
        # Ghi lựa chọn trở lại model để giữ qua các trang
        self._app._selected[self.global_index] = self.selected.get()

# ── Main App ─────────────────────────────────────────────────────────────────

class App(ctk.CTk):

    # Email tài khoản Drive đang dùng ("" = chưa rõ); luồng batch chỉ đọc.
    _drive_active = ""
    _drive_has_unknown = False  # có token từ bản cũ chưa rõ email
    _drive_list_gen = 0     # bỏ kết quả đọc danh sách tài khoản đã cũ

    def __init__(self, license_data: dict | None = None,
                 update_info: dict | None = None):
        super().__init__()
        self.withdraw()  # Chỉ hiện app sau khi xử lý yêu cầu cập nhật bắt buộc.
        _apply_linux_x11_fix(self)
        # Belt-and-suspenders: vô hiệu hoá vòng kiểm tra DPI định kỳ ở instance level
        # (bổ sung cho class-level patch trong _apply_linux_x11_fix)
        if sys.platform != "win32":
            try:
                self._check_dpi_scaling = lambda: None
            except Exception:
                pass
        self._license_data = license_data or {}
        self._update_required = False
        self._updater_active = False
        self._updater_process = None
        self._updater_job = None
        self._update_dialog = None
        self._notified_versions = set()
        self.title(APP_TITLE)
        self.geometry("1220x820")
        self.minsize(960, 640)
        self._set_app_icon()

        self._videos:    list[dict]     = []
        self._rows:      list[VideoRow] = []     # các dòng đang hiển thị (1 trang)
        # Trạng thái theo từng video — giữ độc lập với widget để phân trang được
        self._selected:  list[bool]     = []     # tick chọn / không
        self._status:    list[str]      = []     # "pending"/"done"/...
        self._row_by_index: dict[int, VideoRow] = {}  # global index → widget hiện thị
        self._page       = 0                     # trang đang xem (0-based)
        self._csv_path:  str            = ""
        self._cancel_event = threading.Event()
        # Đang bật = batch tạm dừng: không bắt đầu bước mới, FFmpeg bị treo.
        self._pause_event = threading.Event()
        self._paused_total = 0.0          # tổng giây đã tạm dừng (không tính giờ chạy)
        self._paused_since = None         # mốc monotonic bắt đầu lần tạm dừng hiện tại
        self._processing = False
        self._batch_started_at = None
        self._batch_started_monotonic = None
        self._batch_ended_monotonic = None
        self._batch_clock_job = None
        self._batch_executor = None
        self._batch_futures = []
        self._ui_queue = queue.Queue()
        self._ui_alive = True
        self._ui_drain_job = None

        self._processor  = VideoProcessor()

        self._creds_path   = StringVar(value="")
        self._audio_path   = StringVar(value="")
        self._audio_paths: list[str] = []   # nhiều audio → random cho từng video
        self._logo_path    = StringVar(value="")
        self._logo_paths: list[str] = []    # nhiều logo → random cho từng video
        self._csv_out_path = StringVar(value="")
        self._local_dir    = StringVar(value="")

        self._login_in_progress = False
        self._login_cancel_flag = threading.Event()

        self._build_ui()
        self._ui_drain_job = self.after(25, self._drain_ui_queue)
        # Đọc danh sách tài khoản Drive đã lưu + kiểm tra tài khoản đang dùng.
        self.after(300, lambda: self._reload_drive_accounts(check_active=True))

        # ── Trạng thái kiểm tra license định kỳ ──
        self._recheck_job      = None    # id job after() để có thể hủy
        self._recheck_retries  = 0       # đếm số lần retry khi mất mạng
        self._license_locked   = False   # tránh khóa UI nhiều lần
        self._relaunch_license = False   # cờ báo main() mở lại LicenseDialog

        # Hẹn lần kiểm tra đầu tiên (mặc định sau 30 phút)
        self._recheck_job = self.after(_RECHECK_SECONDS * 1000,
                                       self._schedule_recheck)

        self._batch_thread = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._handle_update_response(update_info, at_startup=True)
        self.after(100, lambda: updater.acknowledge_startup(license_mod.APP_VERSION))
        if not self._update_required:
            # CTk trên Windows: mainloop() lần đầu tự withdraw để đổi màu
            # titlebar và KHÔNG hiện lại nếu withdraw() đã gọi trước đó →
            # deiconify ngay ở đây sẽ bị mất, phải hẹn sau khi mainloop chạy.
            self.after(0, self.deiconify)
            self._check_ffmpeg()

    def _on_close(self):
        """Đóng cửa sổ: hỏi lại nếu batch đang chạy (kết quả đã xong vẫn
        được ghi CSV vì main() chờ luồng batch dọn dẹp trước khi thoát)."""
        if self._updater_active:
            return
        if self._processing and not messagebox.askyesno(
                "Đang xử lý",
                "Batch đang chạy. Đóng ứng dụng sẽ dừng xử lý.\n"
                "Các video đã xong vẫn được lưu vào file CSV.\n\n"
                "Bạn có chắc muốn đóng?"):
            return
        self.destroy()

    def wait_batch_finished(self, timeout: float = 120) -> None:
        """Chờ luồng batch (đã được báo dừng) ghi CSV xong rồi mới thoát."""
        thread = self._batch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    # ── App icon ─────────────────────────────────────────────────────────────

    def _set_app_icon(self):
        ico = _resource("video.ico")
        if not ico.exists():
            return
        try:
            self.iconbitmap(str(ico))
            # CTk reapplies its own icon shortly after init — re-set then.
            self.after(250, lambda: self.iconbitmap(str(ico)))
        except Exception:
            pass

    # ── FFmpeg check ─────────────────────────────────────────────────────────

    def _check_ffmpeg(self):
        try:
            subprocess.run([FFMPEG, "-version"], capture_output=True,
                           creationflags=_WIN_FLAGS, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            messagebox.showerror(
                "FFmpeg chưa cài",
                "Không tìm thấy ffmpeg!\n\nChạy lại run.bat để tự động tải,\n"
                "hoặc chạy thủ công:\n  python setup_ffmpeg.py",
            )

    # ── Bắt lỗi trong callback GUI (nút bấm, sự kiện) ──────────────────────────
    def report_callback_exception(self, exc, val, tb_obj):
        """Lỗi Python trong callback → ghi crash.log + hiện hộp thoại, KHÔNG đóng app."""
        tb = "".join(traceback.format_exception(exc, val, tb_obj))
        _write_crash(tb)
        if hasattr(self, "_log"):
            self._log_msg(f"X Lỗi giao diện:\n{tb}", LOG_ERROR)
        if sys.stderr is not None:
            sys.stderr.write(tb)
        try:
            messagebox.showerror(
                "Lỗi", f"{getattr(exc, '__name__', exc)}: {val}\n\n"
                       "Chi tiết đã ghi vào crash.log (cạnh file .AppImage).")
        except Exception:
            pass

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()
        self._build_settings()
        self._build_video_list()
        self._build_bottom()

    # ── Top bar ──────────────────────────────────────────────────────────────

    def _build_topbar(self):
        bar = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color="#1a1a2e")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)

        ctk.CTkLabel(bar, text=f" {APP_TITLE}",
                     font=(_UI_FONT, 18, "bold")).pack(side="left", padx=16)

        self._csv_label = ctk.CTkLabel(bar, text="Chưa import CSV/Excel",
                                        text_color="gray", font=(_UI_FONT, 12))
        self._csv_label.pack(side="left", padx=6)

        ctk.CTkButton(bar, text=" Import CSV/Excel", width=130, height=32,
                      command=self._import_file).pack(side="left", padx=6)

        # CSV output path
        ctk.CTkButton(bar, text=" Lưu CSV", width=110, height=32,
                      command=self._pick_csv_out).pack(side="right", padx=10)

        self._csv_out_label = ctk.CTkLabel(bar, text="Chưa chọn nơi lưu CSV",
                                        text_color="gray", font=(_UI_FONT, 12))
        self._csv_out_label.pack(side="right", padx=6)

    # ── Settings panel ───────────────────────────────────────────────────────

    def _build_settings(self):
        """Cột cài đặt bên trái: chia tab (ít phải cuộn), chữ to, nút Xử lý,
        Tạm dừng và Kết thúc ghim ở dưới cùng nên luôn nhìn thấy."""
        col = ctk.CTkFrame(self, width=_SET_W, fg_color="transparent")
        # Chiếm cả hàng danh sách video lẫn hàng nhật ký → cao hết cửa sổ.
        col.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=(8, 4), pady=8)
        col.pack_propagate(False)

        # ── Nút hành động + bản quyền: ghim đáy cột (pack trước tab) ──
        actions = ctk.CTkFrame(col, fg_color="transparent")
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        btns = ctk.CTkFrame(actions, fg_color="transparent")
        btns.pack(fill="x")
        self._btn_process = ctk.CTkButton(
            btns, text="XỬ LÝ VIDEO", height=46,
            font=(_UI_FONT, 16, "bold"), fg_color="#1565C0",
            command=self._start_processing,
        )
        self._btn_process.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._btn_stop = ctk.CTkButton(
            btns, text="KẾT THÚC", width=100, height=46,
            font=(_UI_FONT, 14, "bold"), fg_color="#8B0000", state="disabled",
            command=self._request_stop,
        )
        self._btn_stop.pack(side="right")
        self._btn_pause = ctk.CTkButton(
            btns, text=_PAUSE_TEXT, width=110, height=46,
            font=(_UI_FONT, 14, "bold"), fg_color=_PAUSE_COLOR, state="disabled",
            command=self._toggle_pause,
        )
        self._btn_pause.pack(side="right", padx=(0, 6))

        data = self._license_data or {}
        key  = data.get("key") or ""
        masked = ("••••" + key[-4:]) if len(key) >= 4 else (key or "N/A")
        expiry = data.get("expire_date") or "Vô thời hạn"
        lic = ctk.CTkFrame(actions, fg_color="transparent")
        lic.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(lic, text=f"Bản quyền: {masked}  ·  Hết hạn: {expiry}",
                     text_color="gray", font=_F_HINT, anchor="w",
                     ).pack(side="left")
        ctk.CTkButton(lic, text="Hủy kích hoạt", width=110, height=28,
                      fg_color="#555", font=_F_HINT,
                      command=self._deactivate_license).pack(side="right")

        # ── Các tab cài đặt ──
        tabs = ctk.CTkTabview(col, corner_radius=8)
        tabs.pack(fill="both", expand=True)
        pages = {}
        for name in _SETTINGS_TABS:
            page = ctk.CTkScrollableFrame(tabs.add(name), fg_color="transparent")
            page.pack(fill="both", expand=True)
            pages[name] = page
        try:  # CTk 5.2 không có tham số segmented_button_font
            tabs._segmented_button.configure(font=(_UI_FONT, 14, "bold"))
        except Exception:
            pass
        tabs.set(_SETTINGS_TABS[0])
        self._settings_tabs = tabs

        self._build_video_tab(pages["Video"])
        self._build_logo_tab(pages["Logo"])
        self._build_audio_tab(pages["Âm thanh"])
        self._build_save_tab(pages["Lưu video"])

    def _build_video_tab(self, page):
        # ── Concurrency (bản thường: cố định trong edition.py, không hiện) ──
        first = True
        if PROFILE["user_can_set_workers"]:
            self._section(page, "Xử lý đồng thời", first=True)
            first = False
            # Mặc định theo CPU máy này (Pro); người dùng vẫn sửa được.
            self._workers = self._labeled_entry(
                page, "Số video cùng lúc:",
                str(edition.default_workers_for(PROFILE, hwinfo.usable_threads())))
        else:
            self._workers = None
        # Giãn cách giữa 2 video không cho chỉnh ở bản nào: lấy từ edition.py.

        # ── Dữ liệu sản phẩm ──
        self._section(page, "Dữ liệu sản phẩm", first=first)
        self._allow_no_product_name_var = BooleanVar(value=True)
        self._allow_no_product_name_switch = ctk.CTkSwitch(
            page, text="Render không có tên sản phẩm", font=_F_SET,
            variable=self._allow_no_product_name_var,
        )
        self._allow_no_product_name_switch.pack(anchor="w", pady=(2, 2))
        self._hint(page, "Bỏ kiểm tra product_name khi bật. Voice AI cần tên "
                         "sản phẩm nên sẽ tắt tùy chọn này.")

        # ── Trim ──
        self._section(page, "Cắt video")
        self._trim_start = self._labeled_entry(page, "Cắt đầu (giây):", "0")
        self._trim_end   = self._labeled_entry(page, "Cắt đuôi (giây):", "0")

        # ── Độ phân giải ──
        self._section(page, "Độ phân giải")
        self._resolution_var = StringVar(value="Giữ nguyên độ phân giải video")
        self._resolution_menu = ctk.CTkOptionMenu(
            page,
            values=["Nâng độ phân giải lên 1080x1920", "Giữ nguyên độ phân giải video"],
            variable=self._resolution_var, height=_ROW_H,
            font=_F_SET, dropdown_font=_F_SET,
        )
        self._resolution_menu.pack(fill="x", pady=(2, 6))

    def _build_logo_tab(self, page):
        self._section(page, "Logo / Watermark", first=True)

        self._logo_label = ctk.CTkLabel(page, text="", text_color="gray",
                                         font=_F_HINT,
                                         wraplength=_SET_WRAP, justify="left")
        self._logo_label.pack(anchor="w", pady=(0, 2))

        # Khung danh sách logo đã thêm (mỗi dòng có nút xoá riêng)
        self._logo_list = ctk.CTkFrame(page, border_width=1,
                                       border_color="#555", corner_radius=6)
        self._logo_list.pack(fill="x", pady=(0, 4))

        row2 = ctk.CTkFrame(page, fg_color="transparent")
        row2.pack(fill="x", pady=2)
        ctk.CTkButton(row2, text="+ Thêm logo", height=_ROW_H, font=_F_SET,
                      command=self._pick_logo).pack(side="left", fill="x",
                                                     expand=True, padx=(0, 4))
        ctk.CTkButton(row2, text="Xoá hết", width=90, height=_ROW_H,
                      fg_color="#555", font=_F_SET,
                      command=self._clear_logo).pack(side="right")
        self._refresh_logo_label()

        self._section(page, "Hiển thị logo")
        self._logo_pos, _ = self._option_row(
            page, "Vị trí:",
            ["Top-Right", "Top-Left", "Bottom-Right", "Bottom-Left", "Center"])

        # Chuyển động: cố định / chạy vòng theo viền / nảy DVD.
        # Vị trí ở trên đóng vai trò điểm xuất phát khi có chuyển động.
        self._logo_motion, _ = self._option_row(
            page, "Chuyển động:",
            ["Cố định", "Chạy vòng theo viền", "Nảy DVD"],
            command=self._toggle_logo_speed)

        # Tốc độ — chỉ hiện khi Chuyển động khác "Cố định"
        self._logo_speed, self._logo_speed_row = self._option_row(
            page, "Tốc độ:", ["Chậm", "Vừa", "Nhanh"])
        self._logo_speed.set("Vừa")
        # Mốc để pack lại Tốc độ đúng chỗ (ngay trên Độ mờ) khi hiện lại
        self._logo_speed_anchor = ctk.CTkFrame(page, height=0,
                                               fg_color="transparent")
        self._logo_speed_anchor.pack(fill="x")

        # Độ mờ — áp dụng cho logo cả khi đứng yên lẫn khi chuyển động
        self._logo_opacity, _ = self._option_row(
            page, "Độ mờ:", ["Rõ", "Mờ vừa", "Mờ nhiều"])

        self._logo_size = self._labeled_entry(page, "Kích thước (%):", "15")
        self._toggle_logo_speed()  # áp dụng trạng thái ẩn ban đầu

    def _build_audio_tab(self, page):
        # ── Audio ──
        self._section(page, "Thêm nhạc", first=True)

        self._audio_label = ctk.CTkLabel(page, text="", text_color="gray",
                                          font=_F_HINT,
                                          wraplength=_SET_WRAP, justify="left")
        self._audio_label.pack(anchor="w", pady=(0, 2))

        # Khung danh sách audio đã thêm (mỗi dòng có nút xoá riêng)
        self._audio_list = ctk.CTkFrame(page, border_width=1,
                                        border_color="#555", corner_radius=6)
        self._audio_list.pack(fill="x", pady=(0, 4))

        row = ctk.CTkFrame(page, fg_color="transparent")
        row.pack(fill="x", pady=2)
        self._btn_pick_audio = ctk.CTkButton(
            row, text="+ Thêm nhạc", height=_ROW_H, font=_F_SET,
            command=self._pick_audio)
        self._btn_pick_audio.pack(side="left", fill="x", expand=True,
                                  padx=(0, 4))
        self._btn_clear_audio = ctk.CTkButton(
            row, text="Xoá hết", width=90, height=_ROW_H, fg_color="#555",
            font=_F_SET, command=self._clear_audio)
        self._btn_clear_audio.pack(side="right")
        self._refresh_audio_label()

        self._mute_var = BooleanVar(value=False)
        self._mute_chk = ctk.CTkCheckBox(page, text="Tắt tiếng (mute)",
                                         font=_F_SET, variable=self._mute_var)
        self._mute_chk.pack(anchor="w", pady=(6, 0))

        # ── Voice AI (prompt -> Gemini -> Google TTS) ──
        self._section(page, "Voice AI")
        self._voiceai_var = BooleanVar(value=False)
        ctk.CTkSwitch(
            page, text="Bật Voice AI (AI viết lời + đọc)", font=_F_SET,
            variable=self._voiceai_var, command=self._toggle_voiceai,
        ).pack(anchor="w", pady=(2, 2))

        self._voiceai_box = ctk.CTkFrame(page, fg_color="transparent")
        # Ẩn ban đầu; hiện khi bật switch

        # AI tạo text: Gemini hoặc ChatGPT
        self._ai_provider, _ = self._option_row(
            self._voiceai_box, "AI tạo text:", ["Gemini", "ChatGPT"],
            command=self._toggle_ai_provider)
        self._ai_provider.set("Gemini")

        # Khối Gemini
        self._gemini_box = ctk.CTkFrame(self._voiceai_box, fg_color="transparent")
        self._gemini_key = self._fullwidth_entry(
            self._gemini_box, "API Key Gemini:",
            placeholder="Dán API key Gemini")
        self._gemini_model = self._fullwidth_entry(
            self._gemini_box, "Model Gemini:",
            placeholder="gemini-3.1-flash-lite", default="gemini-3.1-flash-lite")

        # Khối ChatGPT
        self._chatgpt_box = ctk.CTkFrame(self._voiceai_box, fg_color="transparent")
        self._chatgpt_key = self._fullwidth_entry(
            self._chatgpt_box, "API Key OpenAI:",
            placeholder="sk-...")
        self._chatgpt_model = self._fullwidth_entry(
            self._chatgpt_box, "Model ChatGPT:",
            placeholder="gpt-5-nano", default="gpt-5-nano")

        self._ai_provider_anchor = ctk.CTkFrame(self._voiceai_box, height=0,
                                                fg_color="transparent")
        self._ai_provider_anchor.pack(fill="x")

        # Nhà cung cấp giọng
        self._tts_provider, prow = self._option_row(
            self._voiceai_box, "AI tạo voice:",
            ["Google TTS", "AutoVoice.vn", "Voice API (videoai)"],
            command=self._toggle_tts_provider)
        prow.pack_configure(pady=(10, 3))
        self._tts_provider.set("Google TTS")

        # ── Khối Google TTS ──
        self._google_box = ctk.CTkFrame(self._voiceai_box, fg_color="transparent")
        self._tts_key = self._fullwidth_entry(
            self._google_box, "API Key Google TTS:",
            placeholder="Dán API key Google TTS")
        self._voice_name, vrow = self._option_row(
            self._google_box, "Giọng:", list(VOICE_CHOICES.keys()))
        vrow.pack_configure(pady=(8, 3))
        self._voice_name.set(DEFAULT_VOICE_LABEL)
        self._tts_speed = self._labeled_entry(
            self._google_box, "Tốc độ đọc:", "1.2")

        # ── Khối AutoVoice.vn ──
        self._autovoice_box = ctk.CTkFrame(self._voiceai_box, fg_color="transparent")
        self._autovoice_key = self._fullwidth_entry(
            self._autovoice_box, "X-API-Key:",
            placeholder="Dán API key AutoVoice")
        self._autovoice_voice = self._fullwidth_entry(
            self._autovoice_box, "Giọng (voiceId):",
            placeholder=DEFAULT_AUTOVOICE_VOICE, default=DEFAULT_AUTOVOICE_VOICE)
        self._autovoice_speed = self._labeled_entry(
            self._autovoice_box, "Tốc độ đọc:", "1")

        # ── Khối Voice API (videoai) ──
        self._videoai_box = ctk.CTkFrame(self._voiceai_box, fg_color="transparent")
        self._videoai_key = self._fullwidth_entry(
            self._videoai_box, "X-API-Key:",
            placeholder="Dán API key Voice API")
        self._videoai_voice = self._fullwidth_entry(
            self._videoai_box, "Giọng (voice_name):",
            placeholder=DEFAULT_VIDEOAI_VOICE, default=DEFAULT_VIDEOAI_VOICE)
        self._videoai_speed = self._labeled_entry(
            self._videoai_box, "Tốc độ đọc:", "1")

        # Mốc để pack khối nhà cung cấp đúng chỗ (ngay trên Prompt)
        self._tts_provider_anchor = ctk.CTkFrame(self._voiceai_box, height=0,
                                                 fg_color="transparent")
        self._tts_provider_anchor.pack(fill="x")

        ctk.CTkLabel(self._voiceai_box, text="Prompt tạo lời thoại:",
                     font=_F_SET, anchor="w").pack(anchor="w", pady=(10, 2))
        self._hint(self._voiceai_box,
                   "Chèn dữ liệu CSV bằng ${tên_cột} (vd ${product_name}).")
        self._prompt_box = ctk.CTkTextbox(self._voiceai_box, height=160,
                                          wrap="word", font=_F_SET)
        self._prompt_box.pack(fill="x", pady=(2, 0))
        self._prompt_box.insert("1.0", DEFAULT_PROMPT)

        # Mốc vị trí để pack khối Voice AI đúng chỗ khi bật/tắt switch
        self._voiceai_anchor = ctk.CTkFrame(page, height=0,
                                            fg_color="transparent")
        self._voiceai_anchor.pack(fill="x")
        self._toggle_ai_provider()  # hiện đúng khối AI tạo text ban đầu
        self._toggle_tts_provider()  # hiện đúng khối nhà cung cấp ban đầu
        self._toggle_voiceai()  # áp dụng trạng thái ẩn ban đầu

    def _build_save_tab(self, page):
        # ── Nơi lưu video: Google Drive hoặc Local ──
        self._section(page, "Nơi lưu video", first=True)

        self._save_mode = ctk.CTkSegmentedButton(
            page, values=["Google Drive", "Local"], height=_ROW_H,
            font=_F_SET, command=self._toggle_save_mode,
        )
        self._save_mode.set("Google Drive")
        self._save_mode.pack(fill="x", pady=(0, 8))

        # Khối Drive (hiện khi chọn "Google Drive")
        self._drive_box = ctk.CTkFrame(page, fg_color="transparent")
        self._login_label = ctk.CTkLabel(
            self._drive_box, text="Chưa đăng nhập",
            text_color="gray", font=_F_SET,
            wraplength=_SET_WRAP, justify="left",
        )
        self._login_label.pack(anchor="w", pady=(0, 6))

        # Khung danh sách Gmail đã đăng nhập: bấm email để dùng tài khoản đó,
        # "Thoát" để đăng xuất riêng tài khoản đó.
        ctk.CTkLabel(self._drive_box, text="Tài khoản Google Drive:",
                     font=_F_SET, anchor="w").pack(anchor="w")
        self._drive_list_box = ctk.CTkFrame(self._drive_box, border_width=1,
                                            corner_radius=6)
        self._drive_list_box.pack(fill="x", pady=(2, 2))
        self._hint(self._drive_box, "Bấm vào email để chuyển tài khoản.",
                   pady=(0, 6))
        self._drive_accounts: list = []      # email theo thứ tự trong khung
        self._drive_has_unknown = False      # có token cũ chưa rõ email
        self._render_drive_accounts()

        self._login_btn = ctk.CTkButton(
            self._drive_box, text="+ Thêm tài khoản", height=_ROW_H + 2,
            font=_F_SET, fg_color="#2d6a4f", command=self._login_drive)
        self._login_btn.pack(fill="x", pady=(0, 6))

        # Link đăng nhập (hiện khi đang chờ đăng nhập).  Tool không tự mở
        # trình duyệt: người dùng copy link vào trình duyệt trên máy này.
        self._login_link_box = ctk.CTkFrame(self._drive_box, fg_color="transparent")
        self._hint(self._login_link_box,
                   "Copy link dưới đây, dán vào trình duyệt trên máy này "
                   "rồi đăng nhập Google:")
        self._login_link_entry = ctk.CTkEntry(self._login_link_box, height=_ROW_H,
                                              font=_F_HINT)
        self._login_link_entry.pack(fill="x", pady=(2, 4))
        link_btns = ctk.CTkFrame(self._login_link_box, fg_color="transparent")
        link_btns.pack(fill="x", pady=(0, 6))
        self._login_link_copy_btn = ctk.CTkButton(
            link_btns, text="Copy link", height=_ROW_H, font=_F_SET,
            fg_color="#1f6aa5", command=self._copy_login_link)
        self._login_link_copy_btn.pack(side="left", fill="x", expand=True,
                                       padx=(0, 4))
        ctk.CTkButton(link_btns, text="Hủy", height=_ROW_H, width=90,
                      fg_color="#555", font=_F_SET,
                      command=self._cancel_drive_login).pack(side="right")
        self._login_link = ""
        self._login_link_copy_job = None

        # Khối Local (hiện khi chọn "Local")
        self._local_box = ctk.CTkFrame(page, fg_color="transparent")
        self._local_label = ctk.CTkLabel(
            self._local_box, text="Chưa chọn thư mục",
            text_color="gray", font=_F_SET, wraplength=_SET_WRAP, justify="left",
        )
        self._local_label.pack(anchor="w", pady=(0, 4))
        ctk.CTkButton(self._local_box, text="Chọn thư mục lưu", height=_ROW_H + 2,
                      font=_F_SET, fg_color="#2d6a4f",
                      command=self._pick_local_dir).pack(fill="x", pady=(0, 6))

        # Mốc để pack lại khối chế độ đúng chỗ (ngay trên "Tên folder")
        self._save_anchor = ctk.CTkFrame(page, height=0, fg_color="transparent")
        self._save_anchor.pack(fill="x")

        # Tên folder: dùng chung — Drive = tên folder Drive; Local = tên thư mục con
        self._section(page, "Tên folder lưu")
        self._folder_name = ctk.CTkEntry(page, height=_ROW_H, font=_F_SET,
                                          placeholder_text="VD: Reup_2026")
        self._folder_name.pack(fill="x", pady=(2, 2))
        self._hint(page, "Drive: tên folder tạo trên Drive. Local: tên thư mục "
                         "con trong thư mục lưu.")

        self._toggle_save_mode()  # áp dụng trạng thái hiển thị ban đầu

    # ── Video list ───────────────────────────────────────────────────────────

    def _build_video_list(self):
        container = ctk.CTkFrame(self)
        container.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        container.grid_rowconfigure(1, weight=1)
        container.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(container, height=40, corner_radius=0,
                            fg_color="#1a1a2e")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)

        self._list_title = ctk.CTkLabel(hdr, text="  Danh sách video  (0)",
                                        font=(_UI_FONT, 13, "bold"))
        self._list_title.pack(side="left", padx=12)

        ctk.CTkButton(hdr, text="[v] Tất cả", width=80, height=28,
                      font=(_UI_FONT, 11),
                      command=lambda: self._select_all(True)).pack(
                          side="right", padx=4)
        ctk.CTkButton(hdr, text="[ ] Bỏ hết", width=80, height=28,
                      font=(_UI_FONT, 11), fg_color="#555",
                      command=lambda: self._select_all(False)).pack(
                          side="right", padx=4)

        self._scroll = ctk.CTkScrollableFrame(container, fg_color="#1e1e1e")
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        self._scroll.grid_columnconfigure(0, weight=1)

        self._empty_lbl = ctk.CTkLabel(
            self._scroll,
            text="  Chưa có video\nBấm  'Import CSV'  để bắt đầu",
            font=(_UI_FONT, 14), text_color="gray",
        )
        self._empty_lbl.pack(expand=True, pady=80)

        # ── Thanh phân trang ──
        pager = ctk.CTkFrame(container, height=40, corner_radius=0,
                             fg_color="#1a1a2e")
        pager.grid(row=2, column=0, sticky="ew")
        pager.grid_propagate(False)

        self._btn_prev = ctk.CTkButton(pager, text="< Trước", width=84, height=28,
                                       font=(_UI_FONT, 11), command=self._prev_page)
        self._btn_prev.pack(side="left", padx=(10, 4), pady=6)

        self._btn_next = ctk.CTkButton(pager, text="Sau >", width=84, height=28,
                                       font=(_UI_FONT, 11), command=self._next_page)
        self._btn_next.pack(side="left", padx=4, pady=6)

        self._page_lbl = ctk.CTkLabel(pager, text="", font=(_UI_FONT, 11),
                                      text_color="gray")
        self._page_lbl.pack(side="left", padx=12)

        self._update_pager()

    # ── Bottom bar ───────────────────────────────────────────────────────────

    def _build_bottom(self):
        # Log có phân loại: lưu toàn bộ dòng kèm nhãn, dropdown chỉ lọc hiển thị.
        self._log_entries: list[tuple] = []          # (cat | None, text)
        self._log_filter = StringVar(value=LOG_FILTER_ALL)
        self._log_bar_h = 285                         # chiều cao thanh dưới (kéo được)

        bar = ctk.CTkFrame(self, height=self._log_bar_h, corner_radius=0)
        bar.grid(row=2, column=1, sticky="ew")      # chỉ nằm dưới danh sách video
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_rowconfigure(3, weight=1)            # hàng log co giãn theo bar
        self._bottom_bar = bar

        # (0) Tay nắm kéo: rê lên/xuống để phóng to/thu nhỏ khối log
        grip = ctk.CTkFrame(bar, height=7, corner_radius=0, fg_color="#4a4a4a")
        grip.grid(row=0, column=0, sticky="ew")
        grip.bind("<Enter>", lambda e: grip.configure(fg_color="#6a6a6a"))
        grip.bind("<Leave>", lambda e: grip.configure(fg_color="#4a4a4a"))
        grip.bind("<B1-Motion>", self._on_log_resize)
        try:
            grip.configure(cursor="sb_v_double_arrow")
        except Exception:   # noqa: BLE001
            pass

        # (1) Tiến độ tổng
        prow = ctk.CTkFrame(bar, fg_color="transparent")
        prow.grid(row=1, column=0, sticky="ew", padx=12, pady=(8, 2))
        prow.grid_columnconfigure(0, weight=1)

        self._progress_lbl = ctk.CTkLabel(prow, text="Sẵn sàng",
                                           anchor="w", font=(_UI_FONT, 12))
        self._progress_lbl.grid(row=0, column=0, sticky="w")

        self._batch_time_lbl = ctk.CTkLabel(
            prow, text="Bắt đầu: --  |  Đã chạy: 00:00:00",
            anchor="w", font=(_UI_FONT, 11), text_color="gray",
        )
        self._batch_time_lbl.grid(row=1, column=0, columnspan=2, sticky="w")

        self._progress_bar = ctk.CTkProgressBar(prow, height=12)
        self._progress_bar.grid(row=2, column=0, sticky="ew", pady=(2, 0))
        self._progress_bar.set(0)

        self._progress_pct = ctk.CTkLabel(prow, text="0 %", width=55,
                                           font=(_UI_FONT, 11))
        self._progress_pct.grid(row=2, column=1, padx=8)

        # (2) Hàng tiêu đề log + dropdown lọc
        hrow = ctk.CTkFrame(bar, fg_color="transparent")
        hrow.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 0))
        hrow.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hrow, text="Nhật ký:", font=(_UI_FONT, 12)).grid(
            row=0, column=0, sticky="w")
        ctk.CTkOptionMenu(
            hrow, variable=self._log_filter, values=list(LOG_FILTER_OPTS),
            width=140, height=26, font=(_UI_FONT, 11),
            command=lambda _v: self._render_log(),
        ).grid(row=0, column=2, sticky="e")

        # (3) Khung log
        self._log = ctk.CTkTextbox(bar, height=180, font=(_UI_MONO_FONT, 12),
                                    state="disabled")
        self._log.grid(row=3, column=0, sticky="nsew", padx=12, pady=(2, 8))

    # ── Resize khối log ────────────────────────────────────────────────────────

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        hours, remainder = divmod(max(0, int(seconds)), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _refresh_batch_clock(self):
        """Update elapsed time once a second on the Tk thread."""
        self._batch_clock_job = None
        if not self._ui_alive or self._batch_started_monotonic is None:
            return
        end = self._batch_ended_monotonic
        now = time.monotonic() if end is None else end
        elapsed = now - self._batch_started_monotonic - self._paused_seconds(now)
        started = time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(self._batch_started_at))
        paused_note = "  (Đang tạm dừng)" if end is None and self._pause_event.is_set() else ""
        self._batch_time_lbl.configure(
            text=f"Bắt đầu: {started}  |  Đã chạy: {self._format_elapsed(elapsed)}"
                 + paused_note)
        if end is None:
            self._batch_clock_job = self.after(1000, self._refresh_batch_clock)

    def _finish_batch_clock(self, ended_monotonic: float):
        self._batch_ended_monotonic = ended_monotonic
        if self._batch_clock_job is not None:
            self.after_cancel(self._batch_clock_job)
            self._batch_clock_job = None
        self._refresh_batch_clock()
        elapsed = (ended_monotonic - self._batch_started_monotonic
                   - self._paused_seconds(ended_monotonic))
        self._log_msg(f"Kết thúc tiến trình. Tổng thời gian chạy: {self._format_elapsed(elapsed)}")

    def _paused_seconds(self, until: float) -> float:
        """Số giây batch đã tạm dừng tính tới mốc `until` (trừ khỏi giờ chạy)."""
        total = getattr(self, "_paused_total", 0.0)
        since = getattr(self, "_paused_since", None)
        if since is not None and since < until:
            total += until - since
        return total

    def _stop_batch_clock(self):
        """Freeze the duration when work ends, before completion dialogs open."""
        if self._batch_started_monotonic is None or self._batch_ended_monotonic is not None:
            return
        self._batch_ended_monotonic = time.monotonic()
        self._ui(lambda end=self._batch_ended_monotonic: self._finish_batch_clock(end))

    def _on_log_resize(self, event):
        """Rê tay nắm: đặt lại chiều cao thanh dưới sao cho mép trên bám con trỏ."""
        win_bottom = self.winfo_rooty() + self.winfo_height()
        new_h = win_bottom - event.y_root
        new_h = max(150, min(new_h, self.winfo_height() - 160))
        self._log_bar_h = new_h
        self._bottom_bar.configure(height=new_h)

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _section(parent, text: str, first: bool = False):
        ctk.CTkLabel(parent, text=text, font=_F_SECTION).pack(
            anchor="w", pady=(2 if first else 18, 6))

    @staticmethod
    def _hint(parent, text: str, pady=(0, 4)):
        """Dòng chữ gợi ý màu xám, tự xuống dòng theo bề rộng cột."""
        ctk.CTkLabel(parent, text=text, text_color="gray", font=_F_HINT,
                     wraplength=_SET_WRAP, justify="left", anchor="w",
                     ).pack(anchor="w", pady=pady)

    @staticmethod
    def _labeled_entry(parent, label: str, default: str) -> ctk.CTkEntry:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text=label, font=_F_SET, anchor="w").pack(side="left")
        e = ctk.CTkEntry(row, width=110, height=_ROW_H, font=_F_SET)
        e.pack(side="right")
        e.insert(0, default)
        return e

    @staticmethod
    def _option_row(parent, label: str, values: list, command=None):
        """Nhãn bên trái + menu chọn bên phải. Trả về (menu, dòng)."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text=label, font=_F_SET, anchor="w").pack(side="left")
        menu = ctk.CTkOptionMenu(row, values=values, width=_CTRL_W,
                                 height=_ROW_H, font=_F_SET,
                                 dropdown_font=_F_SET, dynamic_resizing=False,
                                 command=command)
        menu.pack(side="right")
        return menu, row

    @staticmethod
    def _fullwidth_entry(parent, label: str, *, show: str = "",
                         placeholder: str = "", default: str = "") -> ctk.CTkEntry:
        """Label ở trên, ô nhập full-width ở dưới (cho API key / model)."""
        ctk.CTkLabel(parent, text=label, font=_F_SET, anchor="w").pack(
            anchor="w", pady=(8, 2))
        e = ctk.CTkEntry(parent, placeholder_text=placeholder, height=_ROW_H,
                         font=_F_SET, show=show or "")
        e.pack(fill="x")
        if default:
            e.insert(0, default)
        return e

    def _toggle_voiceai(self):
        """Hiện/ẩn Voice AI; khi bật phải có tên sản phẩm và dùng audio AI."""
        on = self._voiceai_var.get()
        if on:
            self._allow_no_product_name_var.set(False)
            self._voiceai_box.pack(fill="x", before=self._voiceai_anchor)
        else:
            self._allow_no_product_name_var.set(True)
            self._voiceai_box.pack_forget()
        # Voice AI ưu tiên tuyệt đối: khoá 'Chọn Audio' / 'Tắt tiếng' (không fallback)
        state = "disabled" if on else "normal"
        for w in (self._btn_pick_audio, self._btn_clear_audio, self._mute_chk,
                  self._allow_no_product_name_switch):
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _toggle_tts_provider(self, *_):
        """Hiện khối Google TTS, AutoVoice hoặc Voice API theo dropdown nhà cung cấp."""
        self._google_box.pack_forget()
        self._autovoice_box.pack_forget()
        self._videoai_box.pack_forget()
        val = self._tts_provider.get()
        if val == "AutoVoice.vn":
            box = self._autovoice_box
        elif val == "Voice API (videoai)":
            box = self._videoai_box
        else:
            box = self._google_box
        box.pack(fill="x", before=self._tts_provider_anchor)

    def _toggle_ai_provider(self, *_):
        """Hiện khối Gemini hoặc ChatGPT theo dropdown AI tạo text."""
        self._gemini_box.pack_forget()
        self._chatgpt_box.pack_forget()
        box = (self._chatgpt_box
               if self._ai_provider.get() == "ChatGPT"
               else self._gemini_box)
        box.pack(fill="x", before=self._ai_provider_anchor)

    def _toggle_logo_speed(self, *_):
        """Hiện ô Tốc độ chỉ khi logo có chuyển động (khác 'Cố định')."""
        if self._logo_motion.get() == "Cố định":
            self._logo_speed_row.pack_forget()
        else:
            self._logo_speed_row.pack(fill="x", pady=2,
                                      before=self._logo_speed_anchor)

    def _toggle_save_mode(self, *_):
        """Hiện khối Drive hoặc Local theo nút gạt 'Nơi lưu video'."""
        self._drive_box.pack_forget()
        self._local_box.pack_forget()
        box = (self._local_box if self._save_mode.get() == "Local"
               else self._drive_box)
        box.pack(fill="x", before=self._save_anchor)

    def _deactivate_license(self):
        """Xoá key bản quyền khỏi máy rồi thoát (mở lại sẽ phải nhập key)."""
        if not messagebox.askyesno(
                "Hủy kích hoạt",
                "Xoá key bản quyền khỏi máy này và thoát ứng dụng?\n"
                "Lần mở sau sẽ phải nhập key lại."):
            return
        license_mod.clear_key()
        self.destroy()

    def _ui(self, func):
        """Queue UI work; only the Tk thread ever touches Tk methods."""
        if self._ui_alive:
            self._ui_queue.put(func)

    def _drain_ui_queue(self):
        """Run a bounded number of queued UI callbacks on the Tk thread."""
        if not self._ui_alive:
            return
        for _ in range(200):
            try:
                func = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                func()
            except Exception:
                # A stale widget callback must not stop processing later UI work.
                pass
        try:
            self._ui_drain_job = self.after(25, self._drain_ui_queue)
        except Exception:
            self._ui_alive = False

    # ── Kiểm tra license định kỳ ──────────────────────────────────────────────

    def _schedule_recheck(self):
        """Chạy trên luồng UI. Bắn 1 thread nền gọi API (không làm đơ UI)."""
        self._recheck_job = None
        if self._license_locked or self._update_required or self._updater_active:
            return
        threading.Thread(target=self._run_recheck_bg, daemon=True).start()

    def _run_recheck_bg(self):
        """
        Chạy trên LUỒNG NỀN — tuyệt đối không chạm widget Tkinter ở đây
        (kể cả winfo_exists). Chỉ gọi API rồi đẩy kết quả về luồng UI.
        """
        result = license_mod.recheck_license()       # block ~15s, an toàn vì ở nền
        self._ui(lambda: self._on_recheck_result(result))

    def _on_recheck_result(self, result: dict):
        """Chạy trên luồng UI chính — xử lý 3 kịch bản."""
        if (not self.winfo_exists() or self._license_locked or self._update_required
                or self._updater_active):
            return

        status = result["status"]
        if status == license_mod.RECHECK_VALID:
            # KB1: vẫn hợp lệ → reset retry, hẹn chu kỳ 30' tiếp theo
            self._recheck_retries = 0
            self._license_data = result.get("data") or self._license_data
            self._handle_update_response(result.get("update"))
            if self._update_required:
                return
            self._recheck_job = self.after(_RECHECK_SECONDS * 1000,
                                           self._schedule_recheck)

        elif status == license_mod.RECHECK_CHANGED:
            # A response for an older key must not lock or overwrite a new one.
            self._recheck_job = self.after(1000, self._schedule_recheck)

        elif status == license_mod.RECHECK_STORAGE:
            self._log_msg(result.get("message") or "Không đọc/ghi được dữ liệu bản quyền", LOG_ERROR)
            self._recheck_job = self.after(_RETRY_SECONDS * 1000, self._schedule_recheck)

        elif status == license_mod.RECHECK_NETERR:
            # Lỗi mạng không đồng nghĩa key sai: retry 5', hết lượt về 30'.
            self._recheck_retries += 1
            if self._recheck_retries <= _MAX_RETRIES:
                delay = _RETRY_SECONDS
            else:
                self._recheck_retries = 0
                delay = _RECHECK_SECONDS
            self._recheck_job = self.after(delay * 1000, self._schedule_recheck)

        else:  # RECHECK_INVALID — recheck_license() đã clear_key()
            # KB2: key hết hạn/bị thu hồi
            self._lock_ui_license(
                "Bản quyền của bạn đã hết hạn hoặc bị thu hồi.\n"
                "Vui lòng kích hoạt lại key hợp lệ.")

    def _handle_update_response(self, info: dict | None, *, at_startup: bool = False):
        """Chỉ ép cập nhật lúc mở app; recheck không ngắt phiên đang sử dụng."""
        if not isinstance(info, dict) or not info.get("has_update"):
            return
        if self._update_required or self._license_locked or self._updater_active:
            return
        force = bool(info.get("force_update"))
        if force and not at_startup:
            # Server vẫn xác thực key định kỳ, nhưng bản mới bắt buộc chỉ chặn
            # ở lần mở app sau. Không dừng render, ẩn app hoặc hủy timer ở đây.
            return
        latest = str(info.get("latest_version") or "?")
        if not force and latest in self._notified_versions:
            return
        if self._update_dialog is not None:
            self._update_dialog.destroy()
            self._update_dialog = None
        if force:
            self._update_required = True
            if self._recheck_job is not None:
                self.after_cancel(self._recheck_job)
                self._recheck_job = None
            self._cancel_event.set()
            self._processor.cancel_active()
            for future in list(self._batch_futures):
                future.cancel()
            self._login_cancel_flag.set()
            self._btn_process.configure(state="disabled")
            self.withdraw()

        self._show_update_dialog(info, force)
        self._notified_versions.add(latest)

    def _show_update_dialog(self, info: dict, force: bool):
        dlg = ctk.CTkToplevel(self)
        self._update_dialog = dlg
        dlg.withdraw()
        dlg.title("Yêu cầu cập nhật bắt buộc" if force else "Cập nhật phiên bản")
        dlg.geometry("520x440")
        dlg.resizable(False, False)

        def close_dialog():
            # Chỉ khóa nút đóng SAU khi người dùng bấm cập nhật (_begin_in_app_update).
            if self._updater_active:
                return
            self._update_dialog = None
            dlg.destroy()
            if force:
                # Bản bắt buộc mà không cập nhật thì không dùng tiếp được → thoát app.
                self.destroy()

        if force:
            dlg.transient("")  # Cửa sổ độc lập, vẫn hiện khi app chính bị ẩn.
        else:
            dlg.transient(self)
        dlg.protocol("WM_DELETE_WINDOW", close_dialog)

        ctk.CTkLabel(
            dlg, text="Cần cập nhật để tiếp tục sử dụng" if force else "Đã có phiên bản mới",
            font=(_UI_FONT, 20, "bold"),
        ).pack(pady=(22, 8))
        current = license_mod.APP_VERSION
        latest = str(info.get("latest_version") or "?")
        ctk.CTkLabel(dlg, text=_clean_ui_text(f"v{current}  →  v{latest}", 100),
                     font=(_UI_FONT, 14, "bold")).pack(pady=(0, 12))
        changes = ctk.CTkTextbox(dlg, height=180, wrap="word")
        changes.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        text = _clean_ui_text(str(info.get("changelog") or "Chưa có ghi chú thay đổi."), 10000)
        # Chia nhỏ để tránh request RenderAddGlyphs quá lớn trên X11.
        for offset in range(0, len(text), 200):
            changes.insert("end", text[offset:offset + 200])
        changes.configure(state="disabled")
        url = str(info.get("download_url") or "").strip()
        try:
            parsed_url = urlparse(url)
            valid_url = parsed_url.scheme in ("https", "http") and bool(parsed_url.netloc)
        except ValueError:
            valid_url = False
        ctk.CTkButton(
            dlg, text="Cập nhật bắt buộc ngay" if force else "Cập nhật ngay trong ứng dụng",
            height=38, state="normal" if valid_url else "disabled",
            command=lambda: self._begin_in_app_update(info),
        ).pack(fill="x", padx=24, pady=(0, 8))
        if not valid_url:
            ctk.CTkLabel(dlg, text="Chưa có liên kết tải hợp lệ. Vui lòng liên hệ hỗ trợ.",
                         wraplength=460).pack(pady=(0, 8))
        if force:
            ctk.CTkButton(dlg, text="Thoát ứng dụng", height=32, fg_color="#555",
                          command=close_dialog).pack(fill="x", padx=24, pady=(0, 8))
            ctk.CTkLabel(dlg, text="Phiên bản hiện tại đã ngưng hỗ trợ.",
                         text_color="gray").pack(pady=(0, 14))
        else:
            ctk.CTkButton(dlg, text="Bỏ qua lần này", height=32, fg_color="#555",
                           command=close_dialog).pack(fill="x", padx=24, pady=(0, 16))
        dlg.deiconify()
        if force:
            dlg.wait_visibility()
            dlg.grab_set()
        dlg.lift()
        dlg.focus_force()

    def _begin_in_app_update(self, info: dict):
        if self._updater_active:
            return
        self._updater_active = True
        if self._recheck_job is not None:
            self.after_cancel(self._recheck_job)
            self._recheck_job = None
        self._cancel_event.set()
        self._processor.cancel_active()
        for future in list(self._batch_futures):
            future.cancel()
        self._login_cancel_flag.set()
        self._btn_process.configure(state="disabled")
        dlg = self._update_dialog
        # Đã bấm cập nhật → khóa nút đóng. Không đổi transient ở đây: Windows
        # unmap/map lại cửa sổ gây nháy; dialog bị hủy trước khi app chính ẩn.
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)
        for child in dlg.winfo_children():
            child.destroy()
        ctk.CTkLabel(dlg, text="Đang chuẩn bị cập nhật", font=(_UI_FONT, 20, "bold")).pack(pady=(35, 20))
        self._update_progress_text = ctk.CTkLabel(
            dlg, text="Đang dừng tác vụ và lưu kết quả…", wraplength=460)
        self._update_progress_text.pack(padx=24, pady=12)
        self._update_progress_bar = ctk.CTkProgressBar(dlg, mode="indeterminate")
        self._update_progress_bar.pack(fill="x", padx=24, pady=16)
        self._update_progress_bar.start()
        ctk.CTkLabel(
            dlg, text="Cửa sổ tiến trình sẽ luôn hiển thị trong lúc cài đặt.\n"
                      "Ứng dụng chính sẽ đóng và tự mở lại khi cập nhật xong.",
            wraplength=460, text_color="gray",
        ).pack(padx=24, pady=12)

        def prepare():
            last_report = [0.0]

            def progress(message, ratio):
                now = time.monotonic()
                if now - last_report[0] < 0.1:
                    return
                last_report[0] = now
                self._ui(lambda m=message, r=ratio: self._show_update_preparation(m, r))

            try:
                job = updater.create_job(info)
                process = updater.launch_helper(job, progress)
                self._ui(lambda: self._monitor_updater(job, process, info))
            except Exception as exc:
                self._ui(lambda e=str(exc): self._restore_update_prompt(info, e))

        threading.Thread(target=prepare, daemon=True).start()

    def _show_update_preparation(self, message: str, ratio):
        if not self._updater_active:
            return
        self._update_progress_text.configure(text=message)
        if ratio is not None:
            self._update_progress_bar.stop()
            self._update_progress_bar.configure(mode="determinate")
            self._update_progress_bar.set(ratio)

    def _monitor_updater(self, job: dict, process, info: dict):
        self._updater_job = job
        self._updater_process = process
        status = updater.read_status(Path(job["job_dir"]))
        state = status.get("state")
        if state == "cancelled" or process.poll() is not None:
            self._restore_update_prompt(info, status.get("message") or
                                        ("" if state == "cancelled" else
                                         "Tiến trình cập nhật chưa mở được. Chi tiết: " + job["job_dir"]))
            return
        if state in ("visible", "waiting_parent", "error"):
            # Independent updater has its own visible window BEFORE this one goes away.
            if self._update_dialog is not None:
                self._update_dialog.destroy()
                self._update_dialog = None
            self.withdraw()
        if state == "waiting_parent":
            thread = self._batch_thread
            if thread is None or not thread.is_alive():
                self.destroy()  # Helper waits for this process to actually exit.
                return
        self.after(200, lambda: self._monitor_updater(job, process, info))

    def _restore_update_prompt(self, info: dict, error: str):
        self._updater_active = False
        self._updater_process = None
        self._updater_job = None
        if self._update_dialog is not None:
            self._update_dialog.destroy()
        self._show_update_dialog(info, self._update_required)
        if not self._update_required:
            self.deiconify()
            self._btn_process.configure(state="normal")
            self._recheck_job = self.after(_RECHECK_SECONDS * 1000, self._schedule_recheck)
        if error:
            messagebox.showerror("Chưa cập nhật được", error, parent=self._update_dialog)

    def _lock_ui_license(self, message: str):
        """Khóa toàn bộ giao diện bằng overlay phủ kín cửa sổ."""
        if self._license_locked:
            return
        self._license_locked = True
        if self._update_dialog is not None:
            self._update_dialog.destroy()
            self._update_dialog = None

        # Hủy job đang chờ để không recheck tiếp
        if self._recheck_job is not None:
            try:
                self.after_cancel(self._recheck_job)
            except Exception:
                pass
            self._recheck_job = None

        # Dừng tác vụ xử lý video đang chạy (nếu có)
        self._cancel_event.set()
        self._processor.cancel_active()
        for future in list(self._batch_futures):
            future.cancel()

        # Overlay che toàn cửa sổ → chặn mọi tương tác bên dưới
        overlay = ctk.CTkFrame(self, fg_color="#0d0d1a")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        ctk.CTkLabel(overlay, text=" Bản quyền không hợp lệ",
                     font=(_UI_FONT, 22, "bold"), text_color="#ff4757"
                     ).pack(pady=(180, 10))
        ctk.CTkLabel(overlay, text=message, font=(_UI_FONT, 14),
                     wraplength=600, justify="center").pack(pady=10)
        ctk.CTkButton(overlay, text="Kích hoạt lại / Thoát",
                      width=240, height=40,
                      command=self._relaunch_to_license).pack(pady=20)

        # Thông báo lỗi rõ ràng cho người dùng
        messagebox.showerror("Bản quyền", message)

    def _relaunch_to_license(self):
        """Đóng App và yêu cầu main() mở lại LicenseDialog."""
        self._relaunch_license = True
        self.destroy()

    def destroy(self):
        # Hủy job đang chờ trước khi đóng để tránh callback trên widget đã hủy
        self._ui_alive = False
        if self._batch_clock_job is not None:
            try:
                self.after_cancel(self._batch_clock_job)
            except Exception:
                pass
            self._batch_clock_job = None
        self._cancel_event.set()
        self._processor.cancel_active()
        for future in list(self._batch_futures):
            future.cancel()
        if getattr(self, "_ui_drain_job", None) is not None:
            try:
                self.after_cancel(self._ui_drain_job)
            except Exception:
                pass
            self._ui_drain_job = None
        if getattr(self, "_recheck_job", None) is not None:
            try:
                self.after_cancel(self._recheck_job)
            except Exception:
                pass
            self._recheck_job = None
        super().destroy()

    def _is_log_match(self, sel: str, cat, text: str) -> bool:
        if sel == LOG_FILTER_ALL:
            return True
        if sel == LOG_ERROR:
            if cat == LOG_ERROR:
                return True
            t = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s?", "", text or "").strip()
            return t.startswith("X") or t.startswith("!") or "lỗi" in t.lower()
        return sel == cat

    def _is_at_bottom(self) -> bool:
        try:
            if hasattr(self._log, "_textbox"):
                _, y2 = self._log._textbox.yview()
            else:
                _, y2 = self._log.yview()
            return y2 >= 0.95
        except Exception:
            return True

    @staticmethod
    def _retry_log_key(text: str):
        """Return a stable key for retry lines whose X/Y counter may change."""
        text = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s?", "", text or "")
        match = _RETRY_LOG_RE.match(text.strip())
        if not match:
            return None
        # The prefix includes the per-video marker (for example [3/50]), so
        # concurrent workers update their own retry line instead of each other.
        prefix = " ".join(match.group("prefix").casefold().split())
        return prefix, int(match.group("total"))

    def _log_update_last(self, cat, text: str):
        """Update a task's latest retry entry and return its old value/index."""
        key = self._retry_log_key(text)
        if key is None:
            return None
        for index in range(len(self._log_entries) - 1, -1, -1):
            old_cat, old_text = self._log_entries[index]
            if old_cat == LOG_ERROR:
                continue
            if self._retry_log_key(old_text) == key:
                self._log_entries[index] = (cat, text)
                return index, (old_cat, old_text)
        return None

    def _trim_ui_logs(self) -> list[int]:
        """Trim only old non-error lines; return their pre-trim list indices."""
        normal_indices = [
            index for index, (cat, _text) in enumerate(self._log_entries)
            if cat != LOG_ERROR
        ]
        remove_count = len(normal_indices) - MAX_UI_LOGS
        if remove_count <= 0:
            return []

        removed_indices = normal_indices[:remove_count]
        removed = set(removed_indices)
        self._log_entries[:] = [
            entry for index, entry in enumerate(self._log_entries)
            if index not in removed
        ]
        return removed_indices

    def _visible_log_line(self, entries: list[tuple], index: int, sel: str) -> int:
        """Map an entry index to its one-based line number in the active filter."""
        return 1 + sum(
            self._is_log_match(sel, cat, text)
            for cat, text in entries[:index]
        )

    @staticmethod
    def _append_error_log(text: str) -> None:
        """Persist the unabridged error message independently from the UI."""
        try:
            path = _crash_dir() / "app_error.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            # Nhiều luồng cùng ghi: khoá để traceback dài không đan xen nhau.
            with _ERROR_LOG_LOCK, path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"[{timestamp}] {str(text).rstrip()}\n")
        except Exception:
            # Logging must never turn a recoverable processing error into a UI
            # crash (for example on a read-only AppImage mount).
            pass

    def _log_add(self, text: str, cat=None):
        """
        Thêm 1 dòng log kèm nhãn phân loại `cat` (None = dòng hệ thống, chỉ hiện
        ở mục "Tất cả"). Chỉ chèn vào textbox khi khớp bộ lọc đang chọn.
        Nếu người dùng đang cuộn lên trên xem log cũ, không kéo xuống dưới.
        """
        text = str(text)
        if "\n" in text:
            self._log_msg(text, cat)
            return
        if text.lstrip().startswith(("X ", "! ")):
            cat = LOG_ERROR
        timestamp = time.strftime("%H:%M:%S")

        def _do():

            if cat == LOG_ERROR:
                self._append_error_log(text)
            disp_text = text.rstrip("\r\n")
            if sys.platform != "win32":
                disp_text = "".join(c for c in disp_text if ord(c) <= 0xFFFF)
            disp_text = f"[{timestamp}] {disp_text}"

            at_bottom = self._is_at_bottom()
            updated = None if cat == LOG_ERROR else self._log_update_last(cat, disp_text)
            if updated is None:
                self._log_entries.append((cat, disp_text))
            entries_before_trim = list(self._log_entries)
            removed_indices = self._trim_ui_logs()
            sel = self._log_filter.get()
            if updated is not None:
                if removed_indices:
                    # Rebuild after trimming so replacement indices stay valid.
                    self._render_log(scroll_to_end=at_bottom)
                    return
                index, old_entry = updated
                old_visible = self._is_log_match(sel, *old_entry)
                new_visible = self._is_log_match(sel, cat, disp_text)
                if old_visible and new_visible:
                    line = self._visible_log_line(
                        entries_before_trim, index, sel
                    )
                    self._log.configure(state="normal")
                    self._log.delete(f"{line}.0", f"{line}.end")
                    self._log.insert(f"{line}.0", disp_text)
                    if at_bottom:
                        self._log.see("end")
                    self._log.configure(state="disabled")
                elif old_visible != new_visible:
                    self._render_log(scroll_to_end=at_bottom)
                return

            removed_lines = []
            for index in removed_indices:
                old_cat, old_text = entries_before_trim[index]
                if self._is_log_match(sel, old_cat, old_text):
                    removed_lines.append(
                        self._visible_log_line(entries_before_trim, index, sel)
                    )

            new_visible = self._is_log_match(sel, cat, disp_text)
            if removed_lines or new_visible:
                self._log.configure(state="normal")
                # Delete bottom-up so earlier Tk line numbers remain valid.
                for line in reversed(removed_lines):
                    self._log.delete(f"{line}.0", f"{line + 1}.0")
                if new_visible:
                    full_text = disp_text + "\n"
                    chunk_size = 200
                    for i in range(0, len(full_text), chunk_size):
                        self._log.insert("end", full_text[i:i+chunk_size])

                if at_bottom:
                    self._log.see("end")
                self._log.configure(state="disabled")
        self._ui(_do)

    def _log_msg(self, msg: str, cat=None):
        """Tương thích cũ: tách chuỗi nhiều dòng thành từng dòng log."""
        for line in str(msg).split("\n"):
            self._log_add(line, cat)

    def _warn_expired_links(self, videos: list[dict]) -> None:
        """Cảnh báo sớm link TikTok CDN đã/sắp hết hạn (CDN sẽ trả 403)."""
        now = time.time()
        expired = soon = 0
        first_expiry = None
        for vid in videos:
            expiry = link_expiry((vid.get("video_url") or "").strip())
            if expiry is None:
                continue
            if expiry < now:
                expired += 1
            elif expiry < now + 3600:
                soon += 1
            first_expiry = expiry if first_expiry is None else min(first_expiry, expiry)
        if expired:
            self._log_msg(
                f"! {expired}/{len(videos)} link video ĐÃ HẾT HẠN (theo giờ máy) — "
                "các video này sẽ lỗi khi tải. Nên xuất lại file CSV để có link mới.",
                LOG_ERROR)
        if soon:
            self._log_msg(
                f"! {soon} link video sẽ hết hạn trong 1 giờ tới — nên chạy ngay.")
        elif first_expiry and not expired:
            self._log_msg(
                "  Link video hết hạn sớm nhất lúc "
                f"{time.strftime('%H:%M %d/%m', time.localtime(first_expiry))}"
                " — hãy chạy xong trước giờ này.")

    def _log_exception(self, context: str, exc: BaseException,
                       stage: str | None = None):
        """
        Log hiển thị: 3 dòng tiếng Việt ngắn (nguyên nhân, cách xử lý, mã lỗi).
        app_error.log: thêm traceback + output subprocess đầy đủ cho dev.
        """
        info = explain_error(exc, stage)
        summary = "\n".join([f"X {context}", *info.lines()])
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if isinstance(exc, subprocess.CalledProcessError):
            for label, output in (("stdout", exc.stdout), ("stderr", exc.stderr)):
                if output:
                    if isinstance(output, bytes):
                        output = output.decode("utf-8", errors="replace")
                    details += f"\n{label}:\n{output}\n"
        # Tóm tắt hiện lên UI (và tự ghi vào app_error.log qua _log_add);
        # traceback chỉ ghi vào file cho dev.
        self._log_msg(summary, LOG_ERROR)
        self._append_error_log(
            f"--- Chi tiết kỹ thuật [{info.code}] {context} ---\n{details.rstrip()}\n")

    def _render_log(self, scroll_to_end: bool = True):
        """Dựng lại nội dung textbox theo bộ lọc hiện tại (khi đổi dropdown)."""
        sel = self._log_filter.get()
        lines = [t for c, t in self._log_entries if self._is_log_match(sel, c, t)]
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        if lines:

            full_text = "\n".join(lines) + "\n"
            chunk_size = 200
            for i in range(0, len(full_text), chunk_size):
                self._log.insert("end", full_text[i:i+chunk_size])
        if scroll_to_end:
            self._log.see("end")
        self._log.configure(state="disabled")

    # ── File pickers ─────────────────────────────────────────────────────────

    def _import_file(self):
        if self._processing:
            messagebox.showwarning("Đang xử lý", "Không thể import file mới khi batch đang chạy.")
            return
        path = filedialog.askopenfilename(
            title="Chọn file CSV hoặc Excel",
            filetypes=_file_types(
                ("CSV/Excel", "*.csv *.xlsx *.xls"),
                ("CSV", "*.csv"),
                ("Excel", "*.xlsx *.xls"),
            ),
        )
        if not path:
            return
        try:
            self._videos = _read_table(path)
            if self._videos and "video_url" not in self._videos[0]:
                self._log_msg(
                    "! File không có cột 'video_url' — mọi video sẽ bị bỏ qua. "
                    "Kiểm tra lại tên cột trong file.", LOG_ERROR)
            self._csv_path = path
            # Khởi tạo state song song (mặc định chọn hết, trạng thái chờ)
            self._selected = [True] * len(self._videos)
            self._status   = ["pending"] * len(self._videos)
            self._page     = 0
            self._csv_label.configure(
                text=f"OK  {Path(path).name}  ({len(self._videos)} videos)",
                text_color="#4CAF50",
            )
            self._populate_list()
            self._log_msg(f"OK Import {len(self._videos)} video từ {Path(path).name}")
            self._warn_expired_links(self._videos)
        except Exception as exc:
            self._log_exception(f"Lỗi đọc file: {path}", exc)
            messagebox.showerror("Lỗi đọc File", str(exc))

    def _pick_csv_out(self):
        path = filedialog.asksaveasfilename(
            title="Lưu CSV output",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="video_stats_output.csv",
        )
        if path:
            self._csv_out_path.set(path)
            short = path if len(path) < 45 else "…" + path[-42:]
            self._csv_out_label.configure(text=f"  {short}",
                                           text_color="#4CAF50")

    def _pick_local_dir(self):
        path = filedialog.askdirectory(title="Chọn thư mục lưu video")
        if path:
            self._local_dir.set(path)
            short = path if len(path) < 40 else "…" + path[-37:]
            self._local_label.configure(text=f"  {short}",
                                        text_color="#4CAF50")

    def _pick_audio(self):
        """Chọn 1 hoặc nhiều file audio; chọn thêm lần nữa sẽ cộng dồn vào danh sách."""
        paths = filedialog.askopenfilenames(
            title="Chọn file audio (có thể chọn nhiều)",
            filetypes=_file_types(("Audio", "*.mp3 *.wav *.aac *.m4a *.ogg")),
        )
        if not paths:
            return
        for p in paths:
            if p not in self._audio_paths:
                self._audio_paths.append(p)
        self._audio_path.set(self._audio_paths[0])
        self._mute_var.set(False)
        self._refresh_audio_label()

    def _clear_audio(self):
        self._audio_paths = []
        self._audio_path.set("")
        self._refresh_audio_label()

    def _remove_audio(self, path: str):
        if path in self._audio_paths:
            self._audio_paths.remove(path)
        self._audio_path.set(self._audio_paths[0] if self._audio_paths else "")
        self._refresh_audio_label()

    def _refresh_audio_label(self):
        """Vẽ lại khung danh sách audio + dòng tóm tắt phía trên."""
        paths = self._audio_paths
        for w in self._audio_list.winfo_children():
            w.destroy()

        if not paths:
            self._audio_label.configure(
                text="Chọn nhiều file → random cho từng video",
                text_color="gray")
            ctk.CTkLabel(self._audio_list, text="Chưa có file audio nào",
                         text_color="gray", font=_F_SET,
                         ).pack(anchor="w", padx=8, pady=6)
            return

        self._audio_label.configure(
            text=(f"{len(paths)} audio" + (" (random mỗi video)"
                                            if len(paths) > 1 else "")),
            text_color="#4CAF50")
        for i, p in enumerate(paths, 1):
            item = ctk.CTkFrame(self._audio_list, fg_color="transparent")
            item.pack(fill="x", padx=4, pady=(4 if i == 1 else 0, 2))
            ctk.CTkLabel(item, text=_clean_ui_text(f"{i}. {Path(p).name}", 40),
                         font=_F_SET, anchor="w",
                         ).pack(side="left", fill="x", expand=True, padx=(4, 2))
            ctk.CTkButton(item, text="X", width=30, height=28,
                          fg_color="#555", hover_color="#c62828",
                          command=lambda path=p: self._remove_audio(path),
                          ).pack(side="right")

    def _pick_logo(self):
        """Chọn 1 hoặc nhiều logo; chọn thêm lần nữa sẽ cộng dồn vào danh sách."""
        paths = filedialog.askopenfilenames(
            title="Chọn file logo (có thể chọn nhiều)",
            filetypes=_file_types(("Image", "*.png *.jpg *.jpeg *.webp")),
        )
        if not paths:
            return
        for p in paths:
            if p not in self._logo_paths:
                self._logo_paths.append(p)
        self._logo_path.set(self._logo_paths[0])
        self._refresh_logo_label()

    def _clear_logo(self):
        self._logo_paths = []
        self._logo_path.set("")
        self._refresh_logo_label()

    def _remove_logo(self, path: str):
        if path in self._logo_paths:
            self._logo_paths.remove(path)
        self._logo_path.set(self._logo_paths[0] if self._logo_paths else "")
        self._refresh_logo_label()

    def _refresh_logo_label(self):
        """Vẽ lại khung danh sách logo + dòng tóm tắt phía trên."""
        paths = self._logo_paths
        for w in self._logo_list.winfo_children():
            w.destroy()

        if not paths:
            self._logo_label.configure(
                text="Chọn nhiều file → random cho từng video",
                text_color="gray")
            ctk.CTkLabel(self._logo_list, text="Chưa có file logo nào",
                         text_color="gray", font=_F_SET,
                         ).pack(anchor="w", padx=8, pady=6)
            return

        self._logo_label.configure(
            text=(f"{len(paths)} logo" + (" (random mỗi video)"
                                           if len(paths) > 1 else "")),
            text_color="#4CAF50")
        for i, p in enumerate(paths, 1):
            item = ctk.CTkFrame(self._logo_list, fg_color="transparent")
            item.pack(fill="x", padx=4, pady=(4 if i == 1 else 0, 2))
            ctk.CTkLabel(item, text=_clean_ui_text(f"{i}. {Path(p).name}", 40),
                         font=_F_SET, anchor="w",
                         ).pack(side="left", fill="x", expand=True, padx=(4, 2))
            ctk.CTkButton(item, text="X", width=30, height=28,
                          fg_color="#555", hover_color="#c62828",
                          command=lambda path=p: self._remove_logo(path),
                          ).pack(side="right")

    def _pick_creds(self):
        path = filedialog.askopenfilename(
            title="Chọn file credentials.json",
            filetypes=_file_types(("JSON", "*.json")),
        )
        if path:
            self._creds_path.set(path)
            self._creds_label.configure(
                text=f"  {Path(path).name}", text_color="#4CAF50")

    # ── Drive login ──────────────────────────────────────────────────────────

    def _login_drive(self):
        if self._processing:
            messagebox.showwarning("Đang xử lý", "Không thể đăng nhập Google Drive khi batch đang chạy.")
            return
        # Cancel any in-progress login, then start fresh
        if self._login_in_progress:
            self._login_cancel_flag.set()

        self._login_cancel_flag = threading.Event()
        cancel_flag = self._login_cancel_flag
        self._login_in_progress = True
        self._login_label.configure(
            text="Đang chờ đăng nhập — copy link bên dưới vào trình duyệt…",
            text_color="gray")
        self._hide_login_link()

        def _on_auth_url(url):
            self._ui(lambda u=url: None if cancel_flag.is_set()
                     else self._show_login_link(u))

        def _do():
            try:
                import drive
                email = drive.add_account(should_stop=cancel_flag.is_set,
                                          on_auth_url=_on_auth_url)
                if cancel_flag.is_set():
                    return
                self._login_in_progress = False
                self._ui(lambda e=email: None if cancel_flag.is_set() else [
                    self._hide_login_link(),
                    self._login_label.configure(text=f"OK {e}", text_color="#4CAF50"),
                    self._reload_drive_accounts(),
                    messagebox.showinfo("Đăng nhập thành công OK",
                                        f"Đã đăng nhập và chuyển sang dùng:\n{e}"),
                ])
                self._log_msg(f"OK Drive đăng nhập OK — {email}")
            except Exception as exc:
                if cancel_flag.is_set():
                    return
                self._log_exception("Lỗi đăng nhập Google Drive", exc)
                self._login_in_progress = False
                self._ui(lambda e=exc: None if cancel_flag.is_set() else [
                    self._hide_login_link(),
                    self._reload_drive_accounts(),
                    self._login_label.configure(
                        text="Đăng nhập thất bại", text_color="#f44336"),
                    messagebox.showerror("Lỗi đăng nhập X", str(e)),
                ])
            finally:
                if not cancel_flag.is_set():
                    self._login_in_progress = False

        threading.Thread(target=_do, daemon=True).start()

    def _cancel_drive_login(self):
        """Nút Hủy: thôi chờ đăng nhập, đóng cổng nhận kết quả từ Google."""
        self._login_cancel_flag.set()
        self._login_in_progress = False
        self._hide_login_link()
        self._log_msg(" Đã hủy đăng nhập Google Drive.")
        self._reload_drive_accounts()

    # ── Nhiều tài khoản Drive ──

    def _reload_drive_accounts(self, check_active: bool = False):
        """Đọc lại danh sách tài khoản (luồng nền) rồi cập nhật menu.

        ``check_active``: hỏi Google email của tài khoản đang dùng (lúc mở
        app) để hiện đúng trạng thái và ghi nhận token từ bản cũ.
        """
        self._drive_list_gen += 1
        gen = self._drive_list_gen

        def _apply(info):
            if gen == self._drive_list_gen:
                self._apply_drive_accounts(info)

        def _do():
            import drive
            try:
                info = drive.list_accounts()
            except Exception as exc:
                self._log_exception("Lỗi đọc danh sách tài khoản Drive", exc)
                return
            self._ui(lambda i=info: _apply(i))
            if not (check_active and info["has_token"]):
                return
            try:
                drive.current_account_email()
                info = drive.list_accounts()
            except Exception as exc:
                self._log_msg(f"! Chưa kiểm tra được tài khoản Drive đang dùng: {exc}")
                return
            self._ui(lambda i=info: _apply(i))

        threading.Thread(target=_do, daemon=True).start()

    def _apply_drive_accounts(self, info: dict):
        """Đổ danh sách tài khoản vào khung + nhãn trạng thái (Tk thread)."""
        accounts = list(info.get("accounts") or [])
        active = info.get("active") or ""
        unknown = bool(info.get("has_token")) and not active
        self._drive_accounts = accounts
        self._drive_active = active
        self._drive_has_unknown = unknown
        self._render_drive_accounts()
        if self._login_in_progress:
            return  # giữ nhãn "Đang đăng nhập…"
        if active:
            self._login_label.configure(text=f"OK {active}", text_color="#4CAF50")
        elif unknown:
            self._login_label.configure(text="Đã đăng nhập (chưa rõ email)",
                                        text_color="gray")
        else:
            self._login_label.configure(text="Chưa đăng nhập", text_color="gray")

    def _render_drive_accounts(self):
        """Dựng lại các dòng trong khung danh sách Gmail (Tk thread)."""
        box = self._drive_list_box
        for child in box.winfo_children():
            child.destroy()
        rows = ([("", _UNKNOWN_DRIVE_ACCOUNT)] if self._drive_has_unknown else []) \
            + [(email, email) for email in self._drive_accounts]
        if not rows:
            ctk.CTkLabel(box, text=_NO_DRIVE_ACCOUNT, text_color="gray",
                         font=_F_SET).pack(anchor="w", padx=8, pady=6)
            return
        for email, text in rows:
            active = (email and email == self._drive_active) or (
                not email and self._drive_has_unknown)
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            shown = text if len(text) <= 34 else text[:33] + "…"
            ctk.CTkButton(
                row, text=("● " if active else "○ ") + shown, anchor="w",
                height=32, font=_F_SET,
                fg_color="#2d6a4f" if active else "transparent",
                hover_color="#3a7d5f" if active else ("gray80", "gray30"),
                text_color=("gray10", "gray95") if not active else "white",
                command=lambda e=email: self._switch_drive_account(e),
            ).pack(side="left", fill="x", expand=True, padx=(0, 2))
            ctk.CTkButton(
                row, text="Thoát", width=64, height=32, font=_F_SET,
                fg_color="#555",
                command=lambda e=email: self._logout_drive(e),
            ).pack(side="right")

    def _switch_drive_account(self, choice: str):
        """Bấm vào 1 email trong khung → dùng tài khoản đó."""
        if not choice or choice == self._drive_active:
            return
        if self._processing:
            messagebox.showwarning("Đang xử lý",
                                   "Không thể đổi tài khoản Google Drive khi batch đang chạy.")
            return
        self._login_label.configure(text="Đang chuyển tài khoản…", text_color="gray")

        def _do():
            import drive
            try:
                drive.switch_account(choice)
                self._log_msg(f"OK Đã chuyển sang tài khoản Drive: {choice}")
            except Exception as exc:
                self._log_exception("Lỗi đổi tài khoản Google Drive", exc)
                self._ui(lambda e=exc: messagebox.showerror("Lỗi đổi tài khoản X", str(e)))
            self._ui(self._reload_drive_accounts)

        threading.Thread(target=_do, daemon=True).start()

    def _show_login_link(self, url: str):
        """Hiện link đăng nhập Google kèm nút Copy (chạy trên Tk thread)."""
        self._login_link = url
        entry = self._login_link_entry
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, url)
        entry.configure(state="readonly")
        self._login_link_copy_btn.configure(text="Copy link")
        self._login_link_box.pack(fill="x", after=self._login_btn)

    def _hide_login_link(self):
        self._login_link = ""
        self._login_link_box.pack_forget()

    def _copy_login_link(self):
        if not self._login_link:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self._login_link)
            self.update_idletasks()
        except TclError as exc:
            messagebox.showerror("Lỗi", f"Không copy được link:\n{exc}")
            return
        btn = self._login_link_copy_btn
        btn.configure(text="Đã copy")
        if self._login_link_copy_job is not None:
            try:
                self.after_cancel(self._login_link_copy_job)
            except TclError:
                pass

        def _reset():
            self._login_link_copy_job = None
            try:
                btn.configure(text="Copy link")
            except TclError:
                pass
        self._login_link_copy_job = self.after(1500, _reset)

    def _logout_drive(self, email: str):
        """Nút "Thoát" trên 1 dòng: đăng xuất riêng tài khoản đó.

        ``email`` rỗng = token từ bản cũ chưa rõ email (đang dùng).
        """
        if self._processing:
            messagebox.showwarning("Đang xử lý", "Không thể đăng xuất Google Drive khi batch đang chạy.")
            return
        shown = email or "tài khoản hiện tại"
        is_active = (not email) or email == self._drive_active
        if not messagebox.askyesno(
                "Đăng xuất Google Drive",
                f"Đăng xuất {shown} khỏi tool?"
                + ("\nNếu còn tài khoản khác đã đăng nhập, tool sẽ chuyển sang tài khoản đó."
                   if is_active else "")):
            return
        self._login_label.configure(text="Đang đăng xuất…", text_color="gray")

        def _do():
            import drive
            try:
                # Xoá token đang dùng cũng vô hiệu hoá lượt làm mới token đang dở.
                now = drive.remove_account(email)
                self._log_msg(f" Đã đăng xuất Drive: {shown}"
                              + (f" — chuyển sang {now}" if now else ""))
            except Exception as exc:
                self._log_exception("Lỗi đăng xuất Google Drive", exc)
                self._ui(lambda e=exc: messagebox.showerror("Lỗi đăng xuất X", str(e)))
            self._ui(self._reload_drive_accounts)

        threading.Thread(target=_do, daemon=True).start()

    # ── Video list ───────────────────────────────────────────────────────────

    def _page_count(self) -> int:
        total = len(self._videos)
        return max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    def _populate_list(self):
        # Chỉ dựng widget cho TRANG hiện tại → tránh treo khi CSV có nghìn video
        for w in self._scroll.winfo_children():
            w.destroy()
        self._rows.clear()
        self._row_by_index.clear()

        if not self._videos:
            self._empty_lbl = ctk.CTkLabel(
                self._scroll,
                text="  Chưa có video\nBấm  'Import CSV'  để bắt đầu",
                font=(_UI_FONT, 14), text_color="gray",
            )
            self._empty_lbl.pack(expand=True, pady=80)
            self._update_pager()
            return

        total = len(self._videos)
        self._page = max(0, min(self._page, self._page_count() - 1))
        start = self._page * _PAGE_SIZE
        end   = min(start + _PAGE_SIZE, total)

        self._list_title.configure(text=f"  Danh sách video  ({total})")

        for gidx in range(start, end):
            row = VideoRow(self._scroll, self, self._videos[gidx], gidx)
            row.pack(fill="x", padx=2, pady=1)
            self._rows.append(row)
            self._row_by_index[gidx] = row

        self._update_pager()

    def _update_pager(self):
        total = len(self._videos)
        if total == 0:
            self._page_lbl.configure(text="")
            self._btn_prev.configure(state="disabled")
            self._btn_next.configure(state="disabled")
            return
        pages = self._page_count()
        start = self._page * _PAGE_SIZE + 1
        end   = min((self._page + 1) * _PAGE_SIZE, total)
        self._page_lbl.configure(
            text=f"Trang {self._page + 1}/{pages}   "
                 f"(video {start}–{end} / {total})")
        self._btn_prev.configure(state="normal" if self._page > 0 else "disabled")
        self._btn_next.configure(
            state="normal" if self._page < pages - 1 else "disabled")

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._populate_list()

    def _next_page(self):
        if self._page < self._page_count() - 1:
            self._page += 1
            self._populate_list()

    def _select_all(self, state: bool):
        # Áp cho TẤT CẢ video (mọi trang), không chỉ trang đang hiển thị
        for i in range(len(self._selected)):
            self._selected[i] = state
        for row in self._rows:           # cập nhật checkbox đang hiển thị
            row.selected.set(state)

    # ── Lưu trạng thái xử lý video ──

    def _set_row_status(self, gidx: int, status: str):
        if 0 <= gidx < len(self._status):
            self._status[gidx] = status

    # ── Processing ───────────────────────────────────────────────────────────

    def _start_processing(self):
        if self._license_locked or self._update_required or self._updater_active:
            return
        if not self._videos:
            messagebox.showwarning("Chưa có video", "Import file CSV trước!")
            return
        save_mode = "local" if self._save_mode.get() == "Local" else "drive"
        if not self._folder_name.get().strip():
            messagebox.showwarning(
                "Thiếu tên folder",
                "Nhập tên thư mục lưu video!" if save_mode == "local"
                else "Nhập tên folder trên Google Drive!")
            return
        if save_mode == "local" and not self._local_dir.get():
            messagebox.showwarning("Chưa chọn thư mục",
                                   "Bấm 'Chọn thư mục lưu' để chọn nơi lưu video!")
            return
        if (save_mode == "drive" and not self._drive_active
                and not self._drive_has_unknown):
            # Batch không tự mở đăng nhập nữa → báo ngay khi chưa có tài khoản.
            messagebox.showwarning("Chưa đăng nhập Google Drive",
                                   "Bấm 'Thêm tài khoản' để đăng nhập Google Drive trước!")
            return
        if not self._csv_out_path.get():
            messagebox.showwarning("Chưa chọn nơi lưu CSV",
                                   "Bấm 'Lưu CSV' để chọn nơi lưu file output!")
            return

        # Lấy lựa chọn từ model (toàn bộ video, không chỉ trang đang hiển thị)
        selected = [i for i, sel in enumerate(self._selected) if sel]
        if not selected:
            messagebox.showwarning("Chưa chọn", "Tick chọn ít nhất 1 video!")
            return

        try:
            trim_start = float(self._trim_start.get() or "0")
            trim_end   = float(self._trim_end.get() or "0")
            logo_size  = int(self._logo_size.get() or "15")
            # Mặc định tính theo số luồng CPU tool thật sự dùng được (hwinfo),
            # không phải tổng số luồng của máy.
            auto_workers = edition.default_workers_for(PROFILE, hwinfo.usable_threads())
            if PROFILE["user_can_set_workers"] and self._workers:
                workers = int(self._workers.get() or auto_workers)
            else:
                workers = auto_workers
            workers = max(1, min(PROFILE["max_workers"], workers))
            delay   = PROFILE["default_delay"]
        except ValueError:
            messagebox.showerror("Lỗi", "Giây cắt và % logo phải là số!")
            return

        # Voice AI có độ ưu tiên cao nhất; audio sẽ được sinh riêng cho từng video
        voice_ai = None
        if self._voiceai_var.get():
            tts_provider_choice = self._tts_provider.get()
            ai_provider = self._ai_provider.get()
            if ai_provider == "ChatGPT":
                ai_key = self._chatgpt_key.get().strip()
                ai_model = self._chatgpt_model.get().strip() or "gpt-5-nano"
                key_warn_prefix = "API Key OpenAI"
            else:
                ai_key = self._gemini_key.get().strip()
                ai_model = self._gemini_model.get().strip() or "gemini-3.1-flash-lite"
                key_warn_prefix = "API Key Gemini"

            prompt     = self._prompt_box.get("1.0", "end").strip()

            if tts_provider_choice == "AutoVoice.vn":
                tts_key    = self._autovoice_key.get().strip()
                voice_name = (self._autovoice_voice.get().strip() or DEFAULT_AUTOVOICE_VOICE)
                speed_str  = self._autovoice_speed.get() or "1"
                key_warn   = f"Voice AI cần cả {key_warn_prefix} và X-API-Key AutoVoice!"
                provider   = "autovoice"
            elif tts_provider_choice == "Voice API (videoai)":
                tts_key    = self._videoai_key.get().strip()
                voice_name = (self._videoai_voice.get().strip() or DEFAULT_VIDEOAI_VOICE)
                speed_str  = self._videoai_speed.get() or "1"
                key_warn   = f"Voice AI cần cả {key_warn_prefix} và X-API-Key Voice API!"
                provider   = "videoai"
            else:
                tts_key    = self._tts_key.get().strip()
                voice_name = VOICE_CHOICES.get(
                    self._voice_name.get(), "vi-VN-Standard-C")
                speed_str  = self._tts_speed.get() or "1.2"
                key_warn   = f"Voice AI cần cả {key_warn_prefix} và API Key Google TTS!"
                provider   = "google"

            if not ai_key or not tts_key:
                messagebox.showwarning("Thiếu API key", key_warn)
                return
            if not prompt:
                messagebox.showwarning(
                    "Thiếu prompt", "Nhập prompt để tạo lời thoại!")
                return
            try:
                speed = float(speed_str)
            except ValueError:
                messagebox.showerror("Lỗi", "Tốc độ đọc phải là số!")
                return
            voice_ai = {
                "ai_provider": ai_provider,
                "ai_key":      ai_key,
                "model":       ai_model,
                "provider":    provider,
                "tts_key":     tts_key,
                "voice_name":  voice_name,
                "speed":       speed,
                "prompt":      prompt,
            }

        audio_paths: list[str] = []
        if voice_ai:
            audio_path = None       # sẽ được gán per-row trong worker
        elif self._mute_var.get():
            audio_path = ""
        elif self._audio_paths:
            audio_paths = [p for p in self._audio_paths if os.path.isfile(p)]
            if not audio_paths:
                messagebox.showerror(
                    "Lỗi", "Không tìm thấy file audio nào đã chọn (đã bị xoá/di chuyển?).")
                return
            audio_path = audio_paths[0]   # worker random lại cho từng video
        else:
            audio_path = None

        logo_paths: list[str] = []
        if self._logo_paths:
            logo_paths = [p for p in self._logo_paths if os.path.isfile(p)]
            if not logo_paths:
                messagebox.showerror(
                    "Lỗi", "Không tìm thấy file logo nào đã chọn (đã bị xoá/di chuyển?).")
                return

        settings = {
            "trim_start":    trim_start,
            "trim_end":      trim_end,
            "audio_path":    audio_path,
            "audio_paths":   audio_paths,
            "voice_ai":      voice_ai,
            "allow_no_product_name": (
                self._allow_no_product_name_var.get() and not voice_ai),
            "logo_path":     logo_paths[0] if logo_paths else None,
            "logo_paths":    logo_paths,    # worker random lại cho từng video
            "logo_position": self._logo_pos.get(),
            "logo_size":     logo_size,
            "logo_motion":   {"Cố định": "static",
                              "Chạy vòng theo viền": "perimeter",
                              "Nảy DVD": "bounce"}.get(
                                  self._logo_motion.get(), "static"),
            "logo_speed":    {"Chậm": "slow", "Vừa": "normal",
                              "Nhanh": "fast"}.get(
                                  self._logo_speed.get(), "normal"),
            "logo_opacity":  {"Rõ": "opaque", "Mờ vừa": "medium",
                              "Mờ nhiều": "light"}.get(
                                  self._logo_opacity.get(), "opaque"),
            "force_1080p":   self._resolution_var.get() == "Nâng độ phân giải lên 1080x1920",
            "folder_name":   self._folder_name.get().strip(),
            "csv_out":       self._csv_out_path.get(),
            "workers":       workers,
            "delay":         delay,
            "save_mode":     save_mode,
            "local_dir":     self._local_dir.get(),
        }

        # The batch owns immutable copies.  UI actions after this point cannot
        # redirect a running worker to a newly imported CSV.
        settings["videos_snapshot"] = [dict(video) for video in self._videos]
        self._processing = True
        self._cancel_event.clear()
        self._pause_event.clear()
        self._paused_total = 0.0
        self._paused_since = None
        self._processor.resume_active()
        self._btn_process.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._btn_pause.configure(state="normal", text=_PAUSE_TEXT,
                                  fg_color=_PAUSE_COLOR)
        self._progress_bar.set(0)
        self._progress_pct.configure(text="0 %")

        # Bắt đầu batch mới: bỏ log tiến trình cũ, giữ toàn bộ log lỗi.
        self._log_entries[:] = [
            entry for entry in self._log_entries if entry[0] == LOG_ERROR
        ]
        self._render_log()

        self._batch_started_at = time.time()
        self._batch_started_monotonic = time.monotonic()
        self._batch_ended_monotonic = None
        self._refresh_batch_clock()
        self._progress_lbl.configure(
            text=f"Đã xử lý 0/{len(selected)}  |  Thành công 0  |  Lỗi 0  |  Còn {len(selected)}")
        self._log_msg(f"Bắt đầu tiến trình xử lý {len(selected)} video.")

        self._batch_thread = threading.Thread(
            target=self._run_batch,
            args=(selected, settings),
            daemon=True,
        )
        self._batch_thread.start()

    def _request_stop(self):
        self._cancel_event.set()
        if self._pause_event.is_set():
            self._set_paused(False, log=False)
        self._processor.cancel_active()
        for future in list(self._batch_futures):
            future.cancel()
        self._log_msg("[Ket thuc] Đang kết thúc, video dở sẽ không chạy tiếp…")
        self._btn_stop.configure(state="disabled")
        self._btn_pause.configure(state="disabled")

    def _toggle_pause(self):
        if not self._processing or self._cancel_event.is_set():
            return
        self._set_paused(not self._pause_event.is_set())

    def _set_paused(self, paused: bool, log: bool = True):
        """Tạm dừng / tiếp tục batch (chạy trên luồng Tk).

        Tạm dừng: video chưa bắt đầu và bước kế tiếp của video đang dở đều
        chờ; FFmpeg đang encode bị treo tại chỗ. Tải/upload/tạo voice đang
        chạy dở thì chạy nốt bước đó (ngắt giữa chừng dễ đứt kết nối)."""
        now = time.monotonic()
        if paused:
            self._paused_since = now
            self._pause_event.set()
            self._processor.suspend_active()
            self._btn_pause.configure(text=_RESUME_TEXT, fg_color=_RESUME_COLOR)
            if log:
                self._log_msg("[Tam dung] Đã tạm dừng. Encode đang chạy được giữ "
                              "nguyên; tải/upload dở sẽ chạy nốt rồi chờ. "
                              "Bấm TIẾP TỤC để chạy tiếp.")
        else:
            if self._paused_since is not None:
                self._paused_total += now - self._paused_since
                self._paused_since = None
            self._pause_event.clear()
            self._processor.resume_active()
            self._btn_pause.configure(text=_PAUSE_TEXT, fg_color=_PAUSE_COLOR)
            if log:
                self._log_msg("[Tiep tuc] Tiếp tục xử lý.")

    def _wait_while_paused(self):
        """Luồng nền chờ ở đây khi đang tạm dừng (thoát ngay nếu bấm Dừng)."""
        pause = getattr(self, "_pause_event", None)
        while (pause is not None and pause.is_set()
               and not self._cancel_event.is_set()):
            self._cancel_event.wait(0.2)

    def _end_pause_state(self):
        """Batch kết thúc: bỏ cờ tạm dừng (gọi được từ luồng nền).

        Không đụng _paused_since/_paused_total ở đây: _finish_batch_clock
        (đã xếp hàng trên luồng Tk) còn cần chúng để trừ giờ tạm dừng."""
        pause = getattr(self, "_pause_event", None)
        if pause is not None:
            pause.clear()
        resume = getattr(self._processor, "resume_active", None)
        if resume is not None:
            resume()

    def _reset_pause_button(self):
        self._btn_pause.configure(state="disabled", text=_PAUSE_TEXT,
                                  fg_color=_PAUSE_COLOR)

    # ── Batch runner ─────────────────────────────────────────────────────────

    def _run_batch(self, indices: list[int], settings: dict):
        from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
        from drive import DriveUploader

        save_mode   = settings.get("save_mode", "drive")
        batch_videos = settings["videos_snapshot"]
        total       = len(indices)
        out_refs: dict[int, str] = {}      # id(video_dict) → Drive link / local path
        lock        = threading.Lock()
        done        = 0
        errors      = 0

        # Giới hạn của bản (edition.py) áp ở đây nữa → không vượt được dù
        # settings truyền vào là gì.
        workers     = max(1, min(PROFILE["max_workers"], int(settings.get("workers", 1))))
        delay       = max(0.0, float(settings.get("delay", 0)), PROFILE["default_delay"])
        # Giãn cách KHỞI ĐỘNG: 2 video liên tiếp cách nhau >= `delay` giây khi
        # bắt đầu (tránh dồn request). next_start = mốc monotonic sớm nhất cho
        # video kế; bảo vệ bằng start_lock riêng để không tranh chấp với `lock`.
        start_lock  = threading.Lock()
        next_start  = [0.0]
        pause_event = getattr(self, "_pause_event", None) or threading.Event()

        def _abort(msg: str, exc: BaseException | None = None):
            if exc is not None:
                self._log_exception(msg, exc)
            else:
                self._log_msg(msg, LOG_ERROR)
            self._stop_batch_clock()
            self._processing = False
            self._end_pause_state()
            self._ui(lambda: [
                self._btn_process.configure(state="normal"),
                self._btn_stop.configure(state="disabled"),
                self._reset_pause_button(),
            ])

        # ── Chuẩn bị đích lưu (1 lần, trước khi spawn worker) ──
        folder_id = None                 # Drive
        dest_dir  = None                 # Local
        reserved: set[str] = set()       # tên file đã đặt chỗ (Local, tránh đua worker)

        if save_mode == "local":
            dest_dir = os.path.join(settings["local_dir"],
                                    settings["folder_name"])
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except Exception as exc:
                _abort(f"Không tạo được thư mục lưu: {dest_dir}", exc)
                return
            self._log_msg(f" Lưu video vào: {dest_dir}")
        else:
            try:
                active = self._drive_active
                self._log_msg("@ Đang kết nối Google Drive"
                              + (f" ({active})" if active else "") + "…")
                init_uploader = DriveUploader()
                folder_id = init_uploader.create_folder(
                    settings["folder_name"],
                    should_stop=self._cancel_event.is_set,
                    log=lambda message: self._log_msg(message, LOG_UPLOAD),
                )
                self._log_msg(
                    f"OK Đã tạo folder '{settings['folder_name']}' trên Drive")
            except Exception as exc:
                _abort("Không thể kết nối Google Drive", exc)
                return

        # Local: thư mục tạm nằm ngay trong thư mục đích → bước lưu là đổi
        # tên tức thì (cùng ổ đĩa), không phải chép lại cả video từ ổ C:.
        tmp_dir = tempfile.mkdtemp(prefix=".reup_tmp_",
                                   dir=dest_dir if save_mode == "local" else None)

        # ── Dây chuyền theo khâu ──────────────────────────────────────────────
        # `workers` (người dùng đặt) = số FFmpeg encode chạy đồng thời.
        # Pool có thêm luồng để khâu Tải/Voice chạy TRƯỚC (prefetch) và khâu
        # Upload chạy SAU mà không giữ chỗ của encode → encode luôn có việc,
        # không phải chờ mạng. Mỗi luồng = 1 video đang dở → số file tạm trên
        # đĩa tối đa = pool_size.
        # Số luồng mỗi khâu lấy từ edition.py (Pro: nhanh; thường: giới hạn).
        # Chia luồng CPU cho các FFmpeg: mỗi chỗ encode một số luồng cố định
        # (thay vì FFmpeg nào cũng tự lấy hết luồng rồi giành nhau). Hàng đợi
        # chỗ = giới hạn số encode cùng lúc; RAM trống ít thì bớt chỗ.
        encode_slots = hwinfo.plan_encode_slots(
            hwinfo.usable_threads(), workers, hwinfo.available_ram_mb())
        workers = len(encode_slots)
        _free_slots: queue.Queue = queue.Queue()
        for _slot in encode_slots:
            _free_slots.put(_slot)
        upload_n = PROFILE["upload_concurrency"] if save_mode == "drive" else 0
        _drive_upload_sem = threading.Semaphore(upload_n) if upload_n else None
        _PREFETCH = edition.prefetch_for(PROFILE, workers)   # tăng theo số encode
        # Upload ở pool RIÊNG (Pro + Drive): luồng xử lý encode xong chỉ đẩy
        # file vào hàng đợi rồi đi làm video kế → không giữ chỗ của khâu
        # Tải/Encode khi mạng upload chậm. _upload_slots giới hạn số video đã
        # encode đang chờ/đang upload (file tạm trên đĩa); đầy thì luồng xử lý
        # chờ ở đó (backpressure). Không tách (bản thường): upload xong luồng
        # mới nhận video kế.
        split_upload = PROFILE["split_upload_pool"] and save_mode == "drive"
        upload_pool = (ThreadPoolExecutor(max_workers=upload_n,
                                          thread_name_prefix="upload")
                       if split_upload else None)
        _upload_slots = (threading.Semaphore(upload_n + workers)
                         if split_upload else None)
        pool_size = workers + _PREFETCH
        # Voice AI chạy song song với tải video (chỉ cần dữ liệu CSV).
        voice_pool = (ThreadPoolExecutor(max_workers=pool_size,
                                         thread_name_prefix="voice")
                      if settings.get("voice_ai") else None)
        # Nhiều audio → mỗi video lấy ngẫu nhiên 1 file, chia đều (hết 1 vòng
        # mới lặp lại) để không bị 1 bài lặp liên tục.
        audio_bag = (_AudioBag(settings.get("audio_paths") or [])
                     if not settings.get("voice_ai") else None)
        # Nhiều logo → random cho từng video, chia đều giống audio.
        logo_bag = _AudioBag(settings.get("logo_paths") or [])

        # Mỗi luồng giữ 1 VideoDownloader (tái dùng kết nối HTTP) và 1
        # DriveUploader (khỏi đọc token + dựng service mỗi video). Không chia
        # sẻ giữa các luồng vì Drive client không thread-safe.
        _tls = threading.local()

        def _worker_clients():
            if getattr(_tls, "downloader", None) is None:
                _tls.downloader = VideoDownloader()
            if (save_mode == "drive" and not split_upload
                    and getattr(_tls, "uploader", None) is None):
                _tls.uploader = DriveUploader()
            return _tls.downloader, getattr(_tls, "uploader", None)

        def _upload_client():
            if getattr(_tls, "uploader", None) is None:
                _tls.uploader = DriveUploader()
            return _tls.uploader

        def _report_progress():
            # Xếp hàng cập nhật UI ngay trong lock: hàng đợi UI là FIFO nên
            # các lần cập nhật giữ đúng thứ tự (không bị số cũ ghi đè số mới).
            with lock:
                _d, _e = done, errors
                _c = _d + _e
                pct = _c / total
                self._ui(lambda p=pct, d=_d, e=_e, c=_c: [
                    self._progress_bar.set(p),
                    self._progress_pct.configure(text=f"{int(p*100)} %"),
                    self._progress_lbl.configure(
                        text=f"Đã xử lý {c}/{total}  |  Thành công {d}  |  Lỗi {e}  |  Còn {total-c}"
                    ),
                ])

        def _mark_done(gidx: int, vid: dict, ref: str, idx: int, name: str):
            nonlocal done
            with lock:
                # Key by dict identity → robust against duplicate item_id
                out_refs[id(vid)] = ref
                done += 1
            self._ui(lambda g=gidx: self._set_row_status(g, "done"))
            self._log_msg(f"OK  [{idx+1}/{total}] Xong: {name}", LOG_RESULT)

        def _mark_error(gidx: int, vid: dict, idx: int, item_id: str,
                        p_name: str, exc: BaseException,
                        stage: str | None = None):
            nonlocal errors
            with lock:
                errors += 1
                out_refs[id(vid)] = "Lỗi"   # ghi vào CSV output cột Link Video
            self._ui(lambda g=gidx: self._set_row_status(g, "error"))
            self._log_exception(
                f"[{idx+1}/{total}] Lỗi video (item_id={item_id}): "
                f"{p_name or '(dữ liệu product_name rỗng)'}", exc, stage)

        def _upload_log_for(idx: int):
            def _upload_log(message):
                is_error = message.lstrip().startswith(("X ", "! ")) or "lỗi" in message.lower()
                self._log_msg(f"[{idx+1}/{total}] {message}",
                              LOG_ERROR if is_error else LOG_UPLOAD)
            return _upload_log

        # Chạy trên pool upload riêng (chỉ khi split_upload).
        def upload_one(idx: int, gidx: int, vid: dict, item_id: str,
                       p_name: str, name: str, out_file: str):
            try:
                self._wait_while_paused()
                if self._cancel_event.is_set():
                    self._ui(lambda g=gidx: self._set_row_status(g, "pending"))
                    return
                up_size = _file_size(out_file)
                up_t0 = time.monotonic()
                ref = _upload_client().upload_video(
                    out_file, filename=f"{item_id}.mp4", folder_id=folder_id,
                    progress_cb=None,
                    should_stop=self._cancel_event.is_set,
                    log=_upload_log_for(idx),
                )
                up_stats = _upload_stats(up_size, time.monotonic() - up_t0)
                self._log_add(
                    f"OK  [{idx+1}/{total}] Upload xong{up_stats}: {name}",
                    LOG_UPLOAD)
                # Đã upload xong thì luôn ghi nhận, kể cả khi vừa bấm Dừng —
                # nếu bỏ qua, link mất khỏi CSV và lần chạy sau upload trùng.
                _mark_done(gidx, vid, ref, idx, name)
            except Exception as exc:
                if self._cancel_event.is_set():
                    self._ui(lambda g=gidx: self._set_row_status(g, "pending"))
                    return
                _mark_error(gidx, vid, idx, item_id, p_name, exc, "DRIVE")
            finally:
                _safe_remove(out_file)
                _report_progress()

        def _failed(future) -> bool:
            return (future is not None and future.done()
                    and not future.cancelled() and future.exception() is not None)

        # Cổng giãn cách: đặt chỗ một "khe khởi động" cách video trước >= delay
        # giây, rồi chờ tới khe đó. Atomic dưới start_lock nên dù nhiều luồng gọi
        # cùng lúc, các khe vẫn xếp so le delay giây.
        # Tạm dừng: chờ tới khi Tiếp tục rồi mới đặt khe. Bị tạm dừng lúc đang
        # chờ khe → bỏ khe đó, đặt lại sau khi Tiếp tục, để các video đang
        # chờ không cùng ùa chạy một lúc khi bấm Tiếp tục.
        def _gate_start():
            while True:
                self._wait_while_paused()
                if delay <= 0 or self._cancel_event.is_set():
                    return
                with start_lock:
                    slot = max(time.monotonic(), next_start[0])
                    next_start[0] = slot + delay
                interrupted = False
                while not self._cancel_event.is_set():
                    if pause_event.is_set():
                        interrupted = True
                        break
                    remaining = slot - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.2, remaining))
                if not interrupted:
                    return

        # ── Per-video worker ──────────────────────────────────────────────────
        # idx  = thứ tự trong batch (đặt tên temp + log + tiến độ tổng)
        # gidx = chỉ số video trong self._videos (định tuyến trạng thái/lưu CSV)
        def process_one(idx: int, gidx: int):
            nonlocal done, errors

            if self._cancel_event.is_set():
                return

            vid     = batch_videos[gidx]
            item_id = (vid.get("item_id") or "").strip() or f"video_{idx}"
            url     = (vid.get("video_url") or "").strip()
            val_p   = vid.get("product_name") if vid.get("product_name") is not None else vid.get("productName")
            p_name  = "" if val_p is None or str(val_p).strip().lower() in ("", "nan", "none") else str(val_p).strip()
            name    = p_name if p_name else "(dữ liệu product_name rỗng)"
            # idx is unique within this batch → no temp collision on dup item_id
            tmp_dl  = os.path.join(tmp_dir, f"dl_{idx}.mp4")
            tmp_out = os.path.join(tmp_dir, f"out_{idx}.mp4")
            voice_mp3 = None

            if not url:
                with lock:
                    errors += 1
                    out_refs[id(vid)] = "Lỗi"
                self._ui(lambda g=gidx: self._set_row_status(g, "error"))
                self._log_msg(f"X  [{idx+1}/{total}] Bỏ qua (thiếu video_url): {name}",
                              LOG_ERROR)
                _report_progress()
                return

            voice_future = None              # Voice AI chạy song song với tải
            stage        = None              # bước đang chạy → mã lỗi đúng bước
            handed_off   = False             # đã giao cho pool upload
            voice_abort  = threading.Event() # tải lỗi → dừng voice của video này
            out_mp3      = os.path.join(tmp_dir, f"voice_{idx}.mp3")
            try:
                voice_ai = settings.get("voice_ai")
                if not p_name and (voice_ai or not settings.get("allow_no_product_name", False)):
                    raise ValueError(
                        "Voice AI cần tên sản phẩm: ô 'product_name' bị rỗng hoặc không tồn tại trong CSV/Excel"
                        if voice_ai else
                        "Ô 'product_name' bị rỗng hoặc không tồn tại trong CSV/Excel. "
                        "Điền tên sản phẩm hoặc bật 'Render không có tên sản phẩm'.")

                # Client riêng của luồng (tạo 1 lần, tái dùng cho video sau).
                # Keep this inside the try block so auth/setup failures are
                # surfaced as a normal per-video error.
                worker_downloader, worker_uploader = _worker_clients()
                if self._cancel_event.is_set():
                    return
                # Giãn cách khởi động (hàng đợi liên tục): chờ tới khe của video
                # này để không nổ request cùng lúc với video khác.
                _gate_start()
                if self._cancel_event.is_set():
                    return

                # 1. Voice AI (tuỳ chọn): prompt + dữ liệu CSV → Gemini → TTS.
                #    Chỉ cần dữ liệu CSV nên chạy SONG SONG với tải video.
                #    Nếu lỗi sau toàn bộ retry → bỏ qua video hoàn toàn.
                #    Không fallback về audio gốc.
                row_settings = settings
                if audio_bag:
                    row_settings = dict(settings)    # copy riêng, không sửa settings chung
                    row_settings["audio_path"] = audio_bag.next()
                    self._log_add(
                        f"[{idx+1}/{total}] Audio: {Path(row_settings['audio_path']).name}",
                        LOG_VOICE)
                if len(logo_bag) > 1:
                    if row_settings is settings:
                        row_settings = dict(settings)
                    row_settings["logo_path"] = logo_bag.next()
                    self._log_add(
                        f"[{idx+1}/{total}] Logo: {Path(row_settings['logo_path']).name}",
                        LOG_PROCESS)
                if voice_ai:
                    prompt_txt = voice_ai.get("prompt", "")

                    # Lời thoại sinh xong → log nhãn "Tạo text"; retry Gemini cũng
                    # về "Tạo text", còn lại (TTS) về "Tạo voice".
                    def _on_script(s, i=idx):
                        self._log_add(
                            f'OK  [{i+1}/{total}] Tạo text xong: "{s}"', LOG_TEXT)

                    def _voice_log(m):
                        prefix = f"[{idx+1}/{total}] "
                        msg = m if m.startswith(prefix) else f"{prefix}{m}"
                        is_err = "lỗi" in m.lower() or "fail" in m.lower() or "too large" in m.lower()
                        cat = LOG_ERROR if is_err else (LOG_TEXT if ("Gemini" in m or "ChatGPT" in m) else LOG_VOICE)
                        self._log_msg(msg, cat)

                    voice_future = voice_pool.submit(
                        make_voice,
                        vid, out_mp3,
                        ai_provider=voice_ai.get("ai_provider", "Gemini"),
                        ai_key=voice_ai["ai_key"],
                        tts_key=voice_ai["tts_key"],
                        prompt=prompt_txt,
                        model=voice_ai["model"],
                        provider=voice_ai.get("provider", "google"),
                        voice_name=voice_ai["voice_name"],
                        speaking_rate=voice_ai["speed"],
                        retries=5,
                        tts_retries=5,
                        tts_backoff=1,
                        log=_voice_log,
                        on_script=_on_script,
                        should_stop=lambda: (self._cancel_event.is_set()
                                             or voice_abort.is_set()),
                    )

                # 2. Download (dừng sớm nếu Voice AI đã thất bại)
                def _dl_log(m):
                    prefix = f"[{idx+1}/{total}] "
                    msg = m if m.startswith(prefix) else f"{prefix}{m}"
                    cat = LOG_ERROR if ("lỗi" in m.lower() or "fail" in m.lower() or "không" in m.lower()) else LOG_DOWNLOAD
                    self._log_msg(msg, cat)

                self._ui(lambda g=gidx: self._set_row_status(g, "downloading"))
                stage = "DL"
                try:
                    worker_downloader.download(
                        url, tmp_dl,
                        progress_cb=None,
                        log=_dl_log,
                        should_stop=lambda: (self._cancel_event.is_set()
                                             or _failed(voice_future)),
                    )
                except Exception:
                    if _failed(voice_future):
                        stage = None                     # AI hay TTS: tự đoán
                        raise voice_future.exception()   # lỗi gốc là voice
                    raise
                self._log_add(f"OK  [{idx+1}/{total}] Tải xong: {name}", LOG_DOWNLOAD)
                if self._cancel_event.is_set():
                    return

                if voice_future is not None:
                    self._ui(lambda g=gidx: self._set_row_status(g, "processing"))
                    stage = None
                    voice_future.result()            # chờ voice xong / ném lỗi
                    voice_future = None              # đã dùng → tự dọn ở dưới
                    voice_mp3 = out_mp3
                    row_settings = dict(row_settings)  # copy riêng, giữ logo đã random
                    row_settings["audio_path"] = out_mp3   # thay audio gốc
                    self._log_add(
                        f"OK  [{idx+1}/{total}] Tạo voice xong: {name}", LOG_VOICE)

                if self._cancel_event.is_set():
                    if voice_mp3:
                        _safe_remove(voice_mp3)
                    return

                # 3. FFmpeg
                self._ui(lambda g=gidx: self._set_row_status(g, "processing"))
                stage = "FFMPEG"
                # Tối đa `workers` FFmpeg cùng lúc; các luồng dư (prefetch/
                # upload) chờ ở đây với video đã tải sẵn → encode xong là có
                # video kế tiếp ngay.
                slot = _free_slots.get()
                try:
                    # Video đã tải sẵn (prefetch) không bắt đầu encode khi
                    # đang tạm dừng; process_video tự thoát nếu bấm Dừng.
                    self._wait_while_paused()
                    self._processor.process_video(
                        tmp_dl, tmp_out, row_settings,
                        should_stop=self._cancel_event.is_set,
                        info=worker_downloader.last_info,
                        threads=slot.threads,
                    )
                finally:
                    _free_slots.put(slot)
                self._log_add(f"OK  [{idx+1}/{total}] Xử lý xong: {name}", LOG_PROCESS)
                _safe_remove(tmp_dl)
                if voice_mp3:
                    _safe_remove(voice_mp3)
                self._wait_while_paused()
                if self._cancel_event.is_set():
                    _safe_remove(tmp_out)
                    return

                # 4. Lưu kết quả: upload Drive HOẶC move vào thư mục local
                self._ui(lambda g=gidx: self._set_row_status(g, "uploading"))
                stage = "DRIVE" if save_mode == "drive" else None
                if split_upload:
                    # Giữ 1 chỗ trong hàng đợi upload (chờ nếu đầy), rồi giao
                    # file cho pool upload và trả luồng này về ngay.
                    while not _upload_slots.acquire(timeout=0.2):
                        if self._cancel_event.is_set():
                            _safe_remove(tmp_out)
                            return
                    try:
                        fut = upload_pool.submit(
                            upload_one, idx, gidx, vid, item_id, p_name,
                            name, tmp_out)
                    except BaseException:
                        _upload_slots.release()
                        raise
                    # Nhả chỗ cả khi future bị huỷ (không chạy finally), và
                    # không nuốt lỗi lập trình thoát ra khỏi upload_one.
                    def _upload_done(f):
                        _upload_slots.release()
                        if not f.cancelled() and f.exception() is not None:
                            self._log_exception("Luồng upload dừng bất thường",
                                                f.exception())
                    fut.add_done_callback(_upload_done)
                    handed_off = True
                    return
                if save_mode == "local":
                    # Đặt tên duy nhất trong lock để worker không giành tên nhau
                    with lock:
                        final_path = _reserve_local_path(
                            dest_dir,
                            f"{_safe_filename(item_id) or f'video_{idx}'}.mp4",
                            reserved)
                    shutil.move(tmp_out, final_path)
                    ref = os.path.abspath(final_path)
                    self._log_add(f"OK  [{idx+1}/{total}] Lưu xong: {name}", LOG_UPLOAD)
                else:
                    with _drive_upload_sem:
                        up_size = _file_size(tmp_out)
                        up_t0 = time.monotonic()
                        ref = worker_uploader.upload_video(
                            tmp_out, filename=f"{item_id}.mp4", folder_id=folder_id,
                            progress_cb=None,
                            should_stop=self._cancel_event.is_set,
                            log=_upload_log_for(idx),
                        )
                        up_stats = _upload_stats(up_size, time.monotonic() - up_t0)
                    _safe_remove(tmp_out)
                    self._log_add(
                        f"OK  [{idx+1}/{total}] Upload xong{up_stats}: {name}",
                        LOG_UPLOAD)

                # Đã lưu/upload xong → luôn ghi nhận (xem upload_one).
                _mark_done(gidx, vid, ref, idx, name)

            except Exception as exc:
                _safe_remove(tmp_dl)
                _safe_remove(tmp_out)
                if voice_mp3:
                    _safe_remove(voice_mp3)
                if self._cancel_event.is_set():
                    self._ui(lambda g=gidx: self._set_row_status(g, "pending"))
                    return
                _mark_error(gidx, vid, idx, item_id, p_name, exc, stage)

            finally:
                # Voice còn chạy/chưa dùng (tải lỗi, dừng...) → báo dừng và xoá
                # mp3 khi nó kết thúc, không chặn luồng này chờ.
                if voice_future is not None:
                    voice_abort.set()
                    voice_future.add_done_callback(
                        lambda _f, p=out_mp3: _safe_remove(p))
                # Dừng giữa chừng → trả dòng về "chờ" thay vì kẹt ở trạng
                # thái đang tải/đang xử lý, để biết video nào cần chạy lại.
                if (self._cancel_event.is_set() and not handed_off
                        and id(vid) not in out_refs):
                    self._ui(lambda g=gidx: self._set_row_status(g, "pending"))
                _report_progress()

        # ── Run workers ───────────────────────────────────────────────────────
        # Bọc trong try/finally để LUÔN bật lại nút (kể cả khi Dừng hoặc lỗi),
        # nếu không nút 'XỬ LÝ VIDEO' sẽ kẹt disabled và không chạy lại được.
        try:
            # Hàng đợi liên tục: nạp TẤT CẢ video vào 1 pool `pool_size` luồng;
            # luồng nào xong là bốc video kế tiếp ngay (không chờ theo đợt).
            # Encode/upload bị giới hạn bằng semaphore ở trên. `delay` được áp
            # trong _gate_start() như khoảng giãn cách KHỞI ĐỘNG giữa các video
            # → trải đều request, không dồn cùng lúc.
            with ThreadPoolExecutor(max_workers=pool_size) as executor:
                self._batch_executor = executor
                futures = [
                    executor.submit(process_one, b, gidx)
                    for b, gidx in enumerate(indices)
                ]
                self._batch_futures = futures
                for future in as_completed(futures):
                    try:
                        future.result()
                    except CancelledError:
                        # Cancellation is an expected Stop outcome, not a worker failure.
                        pass
                    except Exception as exc:
                        # This is a final safety net for programming errors that
                        # escaped process_one; never silently lose a video.
                        self._log_exception("Worker dừng bất thường", exc)

            # Chờ các video đã giao cho pool upload upload xong (hoặc tự thoát
            # khi Dừng) trước khi ghi CSV / xoá thư mục tạm.
            if upload_pool:
                upload_pool.shutdown(wait=True)

            # ── Cleanup ───────────────────────────────────────────────────────
            # Chờ các Voice AI còn dở (đã được báo dừng) thoát hẳn trước khi
            # xoá thư mục tạm, tránh chúng ghi mp3 vào thư mục đã xoá.
            if voice_pool:
                voice_pool.shutdown(wait=True)
            _safe_rmdir(tmp_dir)

            # Cập nhật tiến độ lần cuối sau khi mọi luồng đã xong.
            _report_progress()

            csv_saved = None
            if out_refs:
                try:
                    csv_saved = self._write_output_csv(
                        settings["csv_out"], out_refs, batch_videos)
                    if csv_saved and csv_saved != settings["csv_out"]:
                        self._log_msg(
                            f"! File CSV đích đang được mở (Excel?) nên đã lưu "
                            f"sang: {csv_saved}", LOG_ERROR)
                    else:
                        self._log_msg(f"\n Đã lưu CSV: {csv_saved}")
                except Exception as exc:
                    self._log_exception(f"Lỗi lưu CSV: {settings['csv_out']}", exc)
            csv_note = csv_saved or "(lỗi lưu CSV — xem log)"

            self._stop_batch_clock()
            if self._cancel_event.is_set():
                self._log_msg(
                    f"\n[Ket thuc] Đã kết thúc.  {done}/{total} hoàn tất trước khi kết thúc"
                    + (f"  X {errors} lỗi" if errors else "")
                )
            else:
                self._log_msg(
                    f"\n Hoàn thành!  {done}/{total} thành công"
                    + (f"  X {errors} lỗi" if errors else "")
                )
                if done:
                    if save_mode == "local":
                        # after(0): hộp thoại modal không được chạy bên trong
                        # vòng xả hàng đợi UI, nếu không log/nút bị treo tới
                        # khi người dùng đóng hộp thoại.
                        self._ui(lambda d=done, t=total, dd=dest_dir,
                                 cv=csv_note:
                                 self.after(0, self._finish_local, d, t, dd, cv))
                    else:
                        self._ui(lambda d=done, cv=csv_note: self.after(
                            0, lambda: messagebox.showinfo(
                                "Hoàn thành!",
                                f"OK  {d}/{total} video thành công\n"
                                f"@  Folder: {settings['folder_name']}\n"
                                f"  CSV: {cv}",
                            )))
        except Exception as exc:
            self._log_exception("Lỗi chạy batch video", exc)
        finally:
            if upload_pool:
                upload_pool.shutdown(wait=True)
            if voice_pool:
                voice_pool.shutdown(wait=True)
            _safe_rmdir(tmp_dir)
            self._stop_batch_clock()
            self._batch_futures = []
            self._batch_executor = None
            # Reset trạng thái TRƯỚC khi bật lại nút: nếu không, người dùng có
            # thể bấm chạy batch mới rồi luồng cũ mới xoá cờ Dừng / _processing
            # của batch mới.
            self._cancel_event.clear()
            self._processing = False
            self._end_pause_state()
            self._ui(lambda: [
                self._btn_process.configure(state="normal"),
                self._btn_stop.configure(state="disabled"),
                self._reset_pause_button(),
            ])

    def _finish_local(self, done: int, total: int, dest_dir: str, csv_out: str):
        """Thông báo hoàn thành (Local) và hỏi mở thư mục chứa video."""
        open_it = messagebox.askyesno(
            "Hoàn thành!",
            f"OK  {done}/{total} video thành công\n"
            f"  Thư mục: {dest_dir}\n"
            f"  CSV: {csv_out}\n\n"
            "Mở thư mục chứa video?")
        if open_it:
            try:
                if sys.platform == "win32":
                    os.startfile(dest_dir)              # Windows: mở Explorer
                elif sys.platform == "darwin":
                    subprocess.run(["open", dest_dir])  # macOS: Finder
                else:
                    subprocess.run(["xdg-open", dest_dir])  # Linux: file manager
            except Exception as exc:     # noqa: BLE001
                self._log_msg(f"! Không mở được thư mục: {exc}")

    # ── CSV writer ────────────────────────────────────────────────────────────

    def _write_output_csv(self, out_path: str, out_refs: dict,
                          videos: list[dict] | None = None):
        """
        Write a new CSV based on input CSV, replacing 'video_url' column with 'Link Video'
        and adding an empty 'hagtag' column to the right of 'Link Video'.
        - Processed video: Drive link OR local path
        - Error: "Lỗi"
        - Unprocessed/stopped: "" (empty)
        """
        videos = self._videos if videos is None else videos
        if not videos:
            return None

        # Hợp nhất cột của mọi dòng (giữ thứ tự xuất hiện) — không chỉ dòng đầu.
        # Bỏ key None: csv.DictReader gom ô thừa (dòng nhiều dấu phẩy hơn
        # header) vào key None, ghi ra sẽ làm hỏng cả file.
        all_keys = dict.fromkeys(
            k for vid in videos for k in vid.keys() if k is not None)

        # Tạo danh sách fieldnames thay cột video_url bằng "Link Video" và thêm "hagtag" ở bên phải
        fieldnames = []
        for fn in all_keys:
            if fn == "video_url":
                if "Link Video" not in fieldnames:
                    fieldnames.append("Link Video")
                    fieldnames.append("hagtag")
            elif fn not in ("Link Video", "hagtag"):
                fieldnames.append(fn)
        if "Link Video" not in fieldnames:
            fieldnames.append("Link Video")
        if "hagtag" not in fieldnames:
            idx = fieldnames.index("Link Video")
            fieldnames.insert(idx + 1, "hagtag")

        # Write beside the destination then atomically replace it.  A crash or
        # Stop during CSV generation must never leave a half-written output CSV.
        tmp_out = f"{out_path}.{threading.get_ident()}.tmp"
        try:
            with open(tmp_out, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames,
                                        extrasaction="ignore")
                writer.writeheader()
                for vid in videos:
                    row = dict(vid)
                    row.pop("video_url", None)   # Xoá cột video_url
                    if id(vid) in out_refs:
                        row["Link Video"] = out_refs[id(vid)]
                    else:
                        row["Link Video"] = ""
                    row["hagtag"] = ""
                    writer.writerow(row)
            # File đích đang mở trong Excel → Windows chặn ghi đè (WinError
            # 5/32). Thử lại vài lần, rồi lưu sang file khác thay vì mất link.
            for _ in range(5):
                try:
                    os.replace(tmp_out, out_path)
                    return out_path
                except PermissionError:
                    time.sleep(1)
            base, ext = os.path.splitext(out_path)
            fallback = f"{base}_{time.strftime('%Y%m%d_%H%M%S')}{ext or '.csv'}"
            os.replace(tmp_out, fallback)
            return fallback
        finally:
            _safe_remove(tmp_out)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _safe_remove(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _upload_stats(size: int, seconds: float) -> str:
    """' (25.3 MB, 41s, 0.62 MB/s)' — để biết upload chậm do file to hay mạng."""
    if size <= 0:
        return ""
    mb = size / (1024 * 1024)
    seconds = max(seconds, 0.001)
    return f" ({mb:.1f} MB, {seconds:.0f}s, {mb / seconds:.2f} MB/s)"


class _AudioBag:
    """Phát ngẫu nhiên audio cho từng video, chia đều: xáo trộn cả danh sách,
    lấy lần lượt hết 1 vòng rồi mới xáo lại. Thread-safe (nhiều worker)."""

    def __init__(self, paths, rng: random.Random | None = None):
        self._paths = list(paths)
        self._rng = rng or random.Random()
        self._deck: list[str] = []
        self._last = None
        self._lock = threading.Lock()

    def __bool__(self):
        return bool(self._paths)

    def __len__(self):
        return len(self._paths)

    def next(self) -> str:
        with self._lock:
            if not self._deck:
                self._deck = list(self._paths)
                self._rng.shuffle(self._deck)
                # Tránh 2 video liền nhau cùng 1 bài ở chỗ nối giữa 2 vòng.
                if len(self._deck) > 1 and self._deck[-1] == self._last:
                    self._deck[0], self._deck[-1] = self._deck[-1], self._deck[0]
            self._last = self._deck.pop()
            return self._last


def _clean_rows(headers: list, rows) -> list[dict]:
    """Ghép header + dòng thành dict; bỏ dòng trống (toàn ô rỗng/khoảng
    trắng) và ô thừa không có tên cột."""
    out = []
    for r in rows:
        values = ["" if v is None else str(v).strip() for v in r]
        if not any(values):
            continue
        out.append(dict(zip(headers, values)))
    return out


def _read_table(path: str) -> list[dict]:
    """Đọc CSV/Excel thành list dict, chịu được các file xuất từ Excel."""
    ext = Path(path).suffix.lower()
    if ext == ".xls":
        raise ValueError("Không hỗ trợ file .xls (Excel 97-2003). "
                         "Hãy mở bằng Excel và lưu lại dạng .xlsx hoặc .csv.")
    if ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            rows = list(wb.active.iter_rows(values_only=True))
        finally:
            wb.close()   # read_only giữ file mở tới khi close → Excel không lưu được
        if not rows:
            raise ValueError("File Excel trống!")
        headers = [str(h).strip() if h is not None else f"column_{i}"
                   for i, h in enumerate(rows[0])]
        return _clean_rows(headers, rows[1:])

    # CSV: Excel trên Windows tiếng Việt hay lưu cp1258/cp1252 thay vì UTF-8.
    for enc in ("utf-8-sig", "cp1258", "cp1252"):
        try:
            with open(path, encoding=enc, newline="") as fh:
                rows = list(csv.reader(fh))
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Không đọc được bảng mã của file CSV — hãy lưu lại "
                         "dạng 'CSV UTF-8' trong Excel.")
    if not rows:
        return []
    headers = [h.strip() for h in rows[0]]
    # zip() tự cắt ô thừa (dòng nhiều dấu phẩy hơn header) thay vì sinh key
    # None như csv.DictReader — key None làm hỏng file CSV output.
    return _clean_rows(headers, rows[1:])


_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str) -> str:
    """Bỏ ký tự Windows cấm trong tên file (/, :, ?...) — tránh lỗi move,
    ghi nhầm vào alternate data stream (abc:1) hoặc thoát khỏi thư mục đích."""
    return _UNSAFE_FILENAME_RE.sub("_", name or "").strip(" .")[:150]


def _safe_rmdir(path: str):
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _reserve_local_path(dest_dir: str, filename: str, reserved: set) -> str:
    """
    Trả về đường dẫn trong dest_dir chưa tồn tại và chưa bị đặt chỗ, thêm hậu
    tố _1, _2 … trước phần mở rộng khi trùng. Caller phải giữ lock để các worker
    không chọn trùng tên.
    """
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dest_dir, filename)
    i = 1
    while candidate in reserved or os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base}_{i}{ext}")
        i += 1
    reserved.add(candidate)
    return candidate


# ── License gate ────────────────────────────────────────────────────────────

class LicenseDialog(ctk.CTk):
    """Cửa sổ nhập key bản quyền — hiện khi chưa kích hoạt hợp lệ."""

    def __init__(self):
        super().__init__()
        _apply_linux_x11_fix(self)
        if sys.platform != "win32":
            try:
                self._check_dpi_scaling = lambda: None
            except Exception:
                pass
        self.activated = False
        self.activated_data = None
        self.after(100, lambda: updater.acknowledge_startup(license_mod.APP_VERSION))

        self.title("Kích hoạt bản quyền")
        self.geometry("420x300")
        self.resizable(False, False)

        ctk.CTkLabel(self, text=" Kích hoạt bản quyền",
                     font=(_UI_FONT, 18, "bold")).pack(pady=(22, 2))
        ctk.CTkLabel(self, text=APP_TITLE,
                     text_color="gray", font=(_UI_FONT, 12)).pack(pady=(0, 14))

        self._entry = ctk.CTkEntry(self, width=340, height=38,
                                   placeholder_text="Nhập key bản quyền…")
        self._entry.pack(pady=(0, 8))
        self._entry.bind("<Return>", lambda _e: self._activate())

        self._btn = ctk.CTkButton(self, text="Kích hoạt", width=340, height=38,
                                  command=self._activate)
        self._btn.pack(pady=(0, 6))

        self._msg = ctk.CTkLabel(self, text="", text_color="#ff4757",
                                  font=(_UI_FONT, 12), wraplength=360, justify="left")
        self._msg.pack(pady=(0, 6))

        dev = license_mod.get_device_id()
        ctk.CTkLabel(self, text=f"Mã thiết bị: {dev}",
                     text_color="#666", font=(_UI_FONT, 10),
                     wraplength=380, justify="center").pack(side="bottom",
                                                            pady=8)

    def _activate(self):
        key = self._entry.get().strip()
        if not key:
            self._msg.configure(text="Vui lòng nhập key!")
            return
        self._btn.configure(state="disabled", text="Đang kiểm tra…")
        self._msg.configure(text="")

        def work():
            result = license_mod.activate(key)
            self.after(0, lambda: self._done(result))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, result: dict):
        if result.get("is_valid"):
            self.activated = True
            self.activated_data = result.get("data")
            self.destroy()
        else:
            self._msg.configure(
                text=result.get("message") or "Kích hoạt thất bại!")
            self._btn.configure(state="normal", text="Kích hoạt")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from packaging_smoke import run
        raise SystemExit(0 if run(Path(sys.argv[2])) else 1)
    if len(sys.argv) == 3 and sys.argv[1] == "--reup-updater":
        updater.run_worker(Path(sys.argv[2]))
        return
    # Bản KHÁCH: chỉ cho mở 1 instance. Bản PRO: cho mở nhiều instance/tab thoải mái
    if PROFILE["single_instance"] and not single_instance.acquire():
        try:
            root = ctk.CTk()
            _apply_linux_x11_fix(root)
            root.withdraw()
            messagebox.showwarning(
                "Ứng dụng đang chạy",
                f"Ứng dụng {APP_TITLE} đang được mở!\n"
                "Không thể mở nhiều cửa sổ/tab ứng dụng cùng lúc."
            )
            root.destroy()
        except Exception:
            pass
        return

    try:
        # Cổng bản quyền: kiểm tra key lúc mở app (chặt — mất mạng = không vào được)
        while True:
            status = license_mod.check_license()
            if status.get("state_changed"):
                continue
            if status.get("storage_error"):
                root = ctk.CTk()
                root.withdraw()
                messagebox.showerror("Lỗi dữ liệu bản quyền", status["message"], parent=root)
                root.destroy()
                return
            if status["is_valid"]:
                app = App(license_data=status["data"], update_info=status.get("update"))
                app.mainloop()
                # Cửa sổ đã đóng nhưng luồng batch (daemon) có thể còn đang
                # dọn dẹp + ghi CSV — chờ nó xong để không mất kết quả.
                app.wait_batch_finished()
                # App đóng vì bản quyền bị thu hồi lúc đang chạy → quay lại nhập key
                if getattr(app, "_relaunch_license", False):
                    continue
                return                       # người dùng đóng app bình thường → thoát
            elif status.get("network_error"):
                # Có key nhưng không liên lạc được server → hỏi thử lại,
                # không bắt nhập lại key.
                root = ctk.CTk()
                root.withdraw()
                root.after(100, lambda: updater.acknowledge_startup(license_mod.APP_VERSION))
                retry = messagebox.askretrycancel(
                    "Không kết nối được máy chủ bản quyền",
                    "Không thể xác minh bản quyền do lỗi kết nối mạng.\n"
                    "Hãy kiểm tra Internet rồi bấm Thử lại.", parent=root)
                root.destroy()
                if not retry:
                    return
            else:
                dlg = LicenseDialog()
                dlg.mainloop()
                if not dlg.activated:        # đóng cửa sổ mà chưa kích hoạt → thoát
                    return
                # Đã kích hoạt → lặp lại, check_license() lần sau sẽ pass
    finally:
        if PROFILE["single_instance"]:
            single_instance.release()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        # Ghi log ra thư mục ghi được (cạnh .AppImage / exe, hoặc ~/.config) để
        # chẩn đoán khi chạy không có console (double-click không hiện traceback).
        _write_crash(tb)
        # In ra stderr (hiện khi chạy từ terminal)
        if sys.stderr is not None:
            sys.stderr.write(tb)
        # Cố hiển thị hộp thoại nếu Tk còn dùng được
        try:
            messagebox.showerror("Lỗi khởi động", tb)
        except Exception:
            pass
        raise
