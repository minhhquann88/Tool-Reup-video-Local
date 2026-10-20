"""
core.py - VideoProcessor (FFmpeg) + VideoDownloader
"""

from __future__ import annotations

import json

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode


def _app_dir() -> Path:
    """
    Returns where bundled assets (bin/) live.
    - Frozen (PyInstaller 6+): sys._MEIPASS (_internal/ folder)
    - Dev: directory of this file
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent

import requests

from error_text import short_reason

# Hide console window on Windows when spawning FFmpeg
_WIN_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── Local FFmpeg resolution ───────────────────────────────────────────────────
# Prefer bundled bin/<exe> → fall back to system PATH.
# Binary name carries .exe on Windows, no suffix on Linux/macOS.
_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""

def _local_or_system(name: str) -> str:
    """Return path to bundled bin/<name>[.exe] if it exists, else the bare name."""
    exe = name + _EXE_SUFFIX
    local = _app_dir() / "bin" / exe
    return str(local) if local.exists() else exe

FFMPEG  = _local_or_system("ffmpeg")
FFPROBE = _local_or_system("ffprobe")


def _to_float(v):
    if v in (None, "N/A", ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def probe_video(path: str):
    """
    Chạy ffprobe 1 lần, trả {duration, width, height} hoặc None nếu *path*
    không phải video đọc được (không có luồng video / ffprobe lỗi). Bắt mọi
    file hỏng: tải thiếu byte (mất moov atom), trang HTML lỗi lưu nhầm .mp4,
    dữ liệu rác...

    duration = 0.0 nếu không xác định được (một số nguồn stream báo "N/A");
    caller phải tự xử lý trường hợp này.
    """
    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,duration",
        "-of", "json",
        path,
    ]
    try:
        # timeout: file hỏng có thể làm ffprobe treo → kẹt luồng cả batch.
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL,
                           creationflags=_WIN_FLAGS, timeout=60)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout or "{}")
    except Exception:   # noqa: BLE001
        return None

    streams = data.get("streams") or []
    if not streams or streams[0].get("codec_type") != "video":
        return None
    s0 = streams[0]

    # Prefer container duration, fall back to stream duration
    duration = (_to_float(data.get("format", {}).get("duration"))
                or _to_float(s0.get("duration"))
                or 0.0)
    return {
        "duration": duration,
        "width":    int(s0.get("width") or 0) or 1280,
        "height":   int(s0.get("height") or 0),
    }


def _is_non_video_ctype(ctype: str) -> bool:
    """True nếu Content-Type là HTML/JSON/XML/text — không phải file video."""
    ctype = (ctype or "").lower()
    return any(t in ctype for t in ("text/", "html", "json", "xml"))


# Link .mp4 gốc của Shopee nhúng trong trang share-video/sản phẩm.
# Khớp cả dạng escape JSON (https:\/\/...) sau khi gỡ dấu \/.
_SHOPEE_MP4_RE = re.compile(
    r'https?://[^\s"\'<>\\]+?susercontent\.com/[^\s"\'<>\\]+?\.mp4', re.I)

# File ID trong link Google Drive: /file/d/<ID>/... hoặc ?id=<ID>
_GDRIVE_ID_RE = re.compile(r'/file/d/([\w-]+)|[?&]id=([\w-]+)')


def _gdrive_file_id(url: str):
    """Tách file ID từ link Google Drive (mọi dạng /view, open?id=, uc?id=…)."""
    m = _GDRIVE_ID_RE.search(url or "")
    return (m.group(1) or m.group(2)) if m else None


# Link TikTok CDN có mốc hết hạn dạng hex 8 ký tự ngay sau chữ ký 32 ký tự:
#   https://v16m.tiktokcdn-us.com/<sig32>/<expiry_hex8>/video/tos/...
# Quá mốc này CDN luôn trả 403 → không cần tải/thử lại.
_TIKTOK_EXPIRY_RE = re.compile(
    r'tiktokcdn[\w.-]*/[0-9a-f]{32}/([0-9a-f]{8})/', re.I)

# HTTP status là lỗi vĩnh viễn (link hết hạn/bị xoá/bị chặn): không thử lại.
_PERMANENT_HTTP_STATUS = (401, 403, 404, 410)


def link_expiry(url: str):
    """Mốc hết hạn (epoch giây) của link TikTok CDN, None nếu không có."""
    m = _TIKTOK_EXPIRY_RE.search(url or "")
    return int(m.group(1), 16) if m else None


def _to_direct_download_url(url: str) -> str:
    """
    Chuẩn hoá link tải về dạng tải trực tiếp:
      - Google Drive (drive.google.com/.../view, open?id=…) → link tải thẳng
        kèm confirm=t để bỏ qua trang cảnh báo quét virus với file lớn.
      - URL khác giữ nguyên.
    """
    if "drive.google.com" in (url or "") or "drive.usercontent.google.com" in (url or ""):
        fid = _gdrive_file_id(url)
        if fid:
            return ("https://drive.usercontent.google.com/download"
                    f"?id={fid}&export=download&confirm=t")
    return url


def _extract_direct_video_url(html: str):
    """
    Trích link tải video thật từ một trang HTML, trả None nếu không thấy:
      - Google Drive: trang xác nhận file lớn chứa <form> tải về (lấy action +
        các input ẩn như confirm/uuid để dựng lại URL tải).
      - Shopee: link .mp4 (CDN susercontent) nhúng trong trang share-video.
    """
    text = (html or "").replace("\\/", "/")

    # Google Drive: form xác nhận tải file lớn
    fm = re.search(r'<form[^>]+action="([^"]+)"', text)
    if fm and "drive.usercontent.google.com" in fm.group(1):
        action = fm.group(1).replace("&amp;", "&")
        inputs = dict(re.findall(
            r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', text))
        if inputs:
            sep = "&" if "?" in action else "?"
            return action + sep + urlencode(inputs)
        return action

    # Shopee: link .mp4 nhúng trong trang
    m = _SHOPEE_MP4_RE.search(text)
    return m.group(0) if m else None


# Position presets: overlay=X:Y  (W/H = video dims, w/h = overlay dims)
_POSITIONS = {
    "Top-Left":     "10:10",
    "Top-Right":    "W-w-10:10",
    "Bottom-Left":  "10:H-h-10",
    "Bottom-Right": "W-w-10:H-h-10",
    "Center":       "(W-w)/2:(H-h)/2",
}

# ── Logo motion / opacity ─────────────────────────────────────────────────────
# Speed = fraction of video width travelled per second (resolution-independent).
_LOGO_SPEED = {"slow": 0.05, "normal": 0.10, "fast": 0.20}

# Alpha for colorchannelmixer (1.0 = fully opaque = no filter added).
_LOGO_OPACITY = {"opaque": 1.0, "medium": 0.6, "light": 0.3}

# Perimeter walk is clockwise (top→right→bottom→left). d0 = distance along the
# perimeter where the chosen start position sits. a=(W-w), b=(H-h).
_PERIM_START = {
    "Top-Left":     "0",
    "Top-Right":    "(W-w)",
    "Bottom-Right": "((W-w)+(H-h))",
    "Bottom-Left":  "(2*(W-w)+(H-h))",
    "Center":       "0",
}

# Bounce phase so the logo sits at the chosen corner at t=0.
# left → px0=(W-w), right → px0=0; top → py0=(H-h), bottom → py0=0.
_BOUNCE_START = {
    "Top-Left":     ("(W-w)", "(H-h)"),
    "Top-Right":    ("0",     "(H-h)"),
    "Bottom-Left":  ("(W-w)", "0"),
    "Bottom-Right": ("0",     "0"),
    "Center":       ("(W-w)", "(H-h)"),
}


def _logo_overlay_xy(motion: str, speed: str, start_pos: str) -> tuple[str, str]:
    """
    Build ffmpeg overlay (x_expr, y_expr) for a moving logo.

    Expressions use only W,H,w,h,t so they're independent of video resolution
    and logo size. Commas are NOT escaped here — the caller escapes them for
    the filtergraph.
    """
    frac = _LOGO_SPEED.get(speed, 0.10)
    S    = f"({frac}*W)"                       # pixels/second along the path
    a, b = "(W-w)", "(H-h)"

    if motion == "perimeter":
        d0 = _PERIM_START.get(start_pos, "0")
        d  = f"mod({S}*t+{d0},2*(W-w)+2*(H-h))"
        x = (f"if(lt({d},{a}),{d},"
             f"if(lt({d},{a}+{b}),{a},"
             f"if(lt({d},2*{a}+{b}),{a}-({d}-{a}-{b}),0)))")
        y = (f"if(lt({d},{a}),0,"
             f"if(lt({d},{a}+{b}),{d}-{a},"
             f"if(lt({d},2*{a}+{b}),{b},{b}-({d}-2*{a}-{b}))))")
        return x, y

    # bounce (DVD-style triangle wave on each axis)
    px0, py0 = _BOUNCE_START.get(start_pos, ("(W-w)", "(H-h)"))
    x = f"abs(mod({S}*t+{px0},2*(W-w))-(W-w))"
    y = f"abs(mod({S}*t+{py0},2*(H-h))-(H-h))"
    return x, y


class VideoProcessor:
    """Wraps FFmpeg to trim, mute/replace audio, and overlay logo."""

    def __init__(self):
        self._process_lock = threading.Lock()
        self._active_processes: set[subprocess.Popen] = set()

    def cancel_active(self) -> None:
        """Request termination of every FFmpeg process owned by this batch."""
        with self._process_lock:
            processes = list(self._active_processes)
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

    # ── public ──────────────────────────────────────────────────────────────

    def get_video_info(self, video_path: str) -> dict:
        """
        Return {duration: float, width: int, height: int}.
        duration is 0.0 if it can't be determined (some streamed sources
        report "N/A"); callers must handle the unknown-duration case.
        """
        info = probe_video(video_path)
        if info is None:
            raise RuntimeError(
                f"ffprobe không đọc được luồng video: {video_path}")
        return info

    def process_video(self, input_path: str, output_path: str,
                      settings: dict, should_stop=None,
                      info: dict | None = None) -> str:
        """
        Build and run a single FFmpeg command that applies all edits.

        settings keys:
            trim_start   float  - seconds to remove from the beginning
            trim_end     float  - seconds to remove from the end
            audio_path   str|None
                            None  → keep original audio
                            ""    → mute (no audio in output)
                            path  → replace with this audio file
            logo_path    str|None - path to PNG/JPG logo (None = skip)
            logo_position str    - key from _POSITIONS (start point if moving)
            logo_size    int    - logo width as % of video width (1-100)
            logo_motion  str    - "static" | "perimeter" | "bounce"
            logo_speed   str    - "slow" | "normal" | "fast" (motion only)
            logo_opacity str    - "opaque" | "medium" | "light"

        info: kết quả probe_video() đã có sẵn (vd. từ bước tải về) → bỏ qua
        lần ffprobe thứ 2. None → tự probe.
        """
        # Clamp negatives — a negative -ss/-t is invalid for ffmpeg
        trim_start   = max(0.0, float(settings.get("trim_start", 0)))
        trim_end     = max(0.0, float(settings.get("trim_end", 0)))
        audio_path   = settings.get("audio_path", None)   # None / "" / path
        logo_path    = settings.get("logo_path") or None
        logo_pos_key = settings.get("logo_position", "Top-Right")
        logo_pct     = max(1, min(100, int(settings.get("logo_size", 15))))
        logo_motion  = settings.get("logo_motion", "static")
        logo_speed   = settings.get("logo_speed", "normal")
        logo_opacity = settings.get("logo_opacity", "opaque")

        # ── Chất lượng encode (ưu tiên tốc độ, chất lượng gần như không đổi) ──
        # Khi buộc phải re-encode (có logo / có cắt / scale), các tham số này
        # quyết định tốc độ và dung lượng:
        #   ultrafast : bỏ phần phân tích nặng (subme/partitions/B-frame) →
        #               encode nhanh hơn veryfast ~40% (đo 1080x1920).
        #   cabac=1 + deblock=0,0 : bật lại 2 thứ ultrafast tắt nhưng gần như
        #               không tốn tốc độ → file nhỏ hơn ultrafast thuần ~35%,
        #               không bị vỡ khối. Profile thành Main (mọi nền tảng đọc được).
        #   crf 20    : SSIM ~0.994, ngang veryfast crf 18 bằng mắt thường; file
        #               to hơn veryfast crf 18 khoảng 1.8 lần.
        #   192k aac  : audio rõ. Audio gốc không cắt vẫn được copy nguyên vẹn.
        # Có thể override qua settings (video_x264_params="" để bỏ hẳn).
        video_crf     = str(settings.get("video_crf", 20))
        video_preset  = str(settings.get("video_preset", "ultrafast"))
        video_x264_params = str(settings.get("video_x264_params", "cabac=1:deblock=0,0"))
        audio_bitrate = str(settings.get("audio_bitrate", "192k"))

        # ── Force 1080p dọc (1080×1920) ───────────────────────────────────────────
        # Scale video lên đúng chuẩn video dọc mạng xã hội (Shopee, TikTok, Reels).
        # Dùng force_original_aspect_ratio=decrease để giữ tỷ lệ không bị méo,
        # sau đó pad viền đen nếu video gốc không đúng tỷ lệ 9:16.
        force_1080p   = bool(settings.get("force_1080p", False))

        if info is None:
            info = self.get_video_info(input_path)
        duration     = info["duration"]          # 0.0 if unknown
        src_w        = info["width"]
        src_h        = info["height"]

        # Video gốc đã đúng 1080×1920 → scale/pad là no-op, bỏ hẳn để không
        # buộc phải re-encode. Đúng tỷ lệ 9:16 (vd. 720×1280) → chỉ scale,
        # bỏ pad (pad không thêm viền nào).
        scale_vf = None
        if force_1080p and (src_w, src_h) != (1080, 1920):
            scale_vf = "scale=1080:1920:force_original_aspect_ratio=decrease:flags=bicubic"
            if src_w * 1920 != src_h * 1080:
                scale_vf += ",pad=1080:1920:-1:-1:color=black"

        logo_px      = max(1, round(src_w * logo_pct / 100))

        has_logo       = bool(logo_path and os.path.exists(logo_path))
        has_trim       = trim_start > 0 or trim_end > 0
        has_new_audio  = bool(audio_path and os.path.exists(audio_path))

        # We must know the duration to (a) validate the trim, or (b) cap a
        # replacement audio to the video length. Without trim and without new
        # audio, duration is irrelevant.
        need_duration  = has_trim or has_new_audio
        if need_duration and duration <= 0:
            raise RuntimeError(
                "Không đọc được thời lượng video (metadata thiếu) — "
                "không thể cắt hoặc ghép audio cho video này."
            )

        # Output duration (measured from the seek point). None → no -t (full).
        new_duration = None
        use_shortest  = False   # dùng -shortest thay -t khi ghép audio mới
        if has_trim:
            new_duration = duration - trim_start - trim_end
            if new_duration <= 0:
                raise ValueError(
                    f"Trim quá dài! Video chỉ có {duration:.1f}s, "
                    f"trim_start={trim_start}s trim_end={trim_end}s"
                )
        elif has_new_audio:
            # Dùng -shortest (kết thúc theo stream nào ngắn hơn) thay vì
            # -t duration để tránh lỗi khi metadata duration của video gốc
            # không chính xác (thường gặp ở video Shopee/CDN). Nếu audio TTS
            # dài hơn video → bị cắt; nếu ngắn hơn → video kết thúc cùng audio.
            use_shortest = True

        # Re-encode is REQUIRED when cutting the START, because -c:v copy can
        # only start at a keyframe → seeking mid-GOP causes freeze/stutter.
        # Cutting only the END (-t) needs no keyframe → video stays copied.
        # Logo and a real 1080p scale also force a re-encode.
        need_reencode_video = has_logo or trim_start > 0 or scale_vf is not None

        cmd = [FFMPEG, "-y"]

        # ── inputs ──────────────────────────────────────────────────────────
        # Input seeking (-ss before -i) on input 0: fast, and frame-accurate
        # because we always re-encode when trimming. Seeks video + original
        # audio of input 0 TOGETHER, so they stay in sync.
        if trim_start > 0:
            cmd += ["-ss", str(trim_start)]
        cmd += ["-i", input_path]

        audio_idx = None
        logo_idx  = None
        next_idx  = 1

        # Input N: replacement audio (starts at 0:00, not seeked)
        if has_new_audio:
            cmd += ["-i", audio_path]
            audio_idx = next_idx
            next_idx += 1

        # Input N: logo image
        if has_logo:
            cmd += ["-i", logo_path]
            logo_idx = next_idx
            next_idx += 1

        # ── filters ─────────────────────────────────────────────────────────

        if logo_idx is not None:
            # Video source node: thêm scale 1080p dọc vào đầu chain nếu cần.
            if scale_vf:
                video_node = f"[0:v]{scale_vf}[scaled1080]"
                src = "[scaled1080]"
            else:
                video_node = ""
                src = "[0:v]"

            # Logo chain: scale logo, then apply opacity only when not fully opaque.
            alpha = _LOGO_OPACITY.get(logo_opacity, 1.0)
            logo_chain = f"[{logo_idx}:v]scale={logo_px}:-2"
            if alpha < 1.0:
                logo_chain += f",format=rgba,colorchannelmixer=aa={alpha}"
            logo_chain += "[logo]"

            # Position: static preset, or a time-based moving expression.
            if logo_motion in ("perimeter", "bounce"):
                x_expr, y_expr = _logo_overlay_xy(
                    logo_motion, logo_speed, logo_pos_key)
                # Escape commas so the filtergraph keeps them inside the expr.
                x_e = x_expr.replace(",", "\\,")
                y_e = y_expr.replace(",", "\\,")
                overlay = f"overlay=x={x_e}:y={y_e}:eval=frame"
            else:
                pos = _POSITIONS.get(logo_pos_key, "W-w-10:10")
                overlay = f"overlay={pos}"

            if video_node:
                fc = f"{video_node};{logo_chain};{src}[logo]{overlay}[vout]"
            else:
                fc = f"{logo_chain};{src}[logo]{overlay}[vout]"
            cmd += ["-filter_complex", fc, "-map", "[vout]"]
        elif scale_vf:
            # Không có logo nhưng cần scale: dùng -vf đơn giản hơn filter_complex.
            cmd += ["-map", "0:v", "-vf", scale_vf]
        else:
            cmd += ["-map", "0:v"]

        # ── audio mapping ────────────────────────────────────────────────────
        if audio_idx is not None:
            # Replace with new audio file
            cmd += ["-map", f"{audio_idx}:a"]
        elif audio_path is None:
            # Keep original audio (? = optional, won't fail if no audio stream)
            cmd += ["-map", "0:a?"]
        # else audio_path == "" → mute: no -map for audio → no audio stream

        # ── duration ─────────────────────────────────────────────────────────
        # -t limits output length (measured from the seek point). Video is the
        # master: if new audio is shorter, the tail stays silent; if longer,
        # it's cut to match the video. Omitted when full-length (no trim, no
        # new audio) so unknown-duration sources still process.
        # Khi ghép audio mới (không trim): dùng -shortest để tự cắt theo stream
        # ngắn hơn, tránh lỗi tính sai duration từ metadata video gốc.
        if new_duration is not None:
            cmd += ["-t", str(new_duration)]
        elif use_shortest:
            cmd += ["-shortest"]

        # ── codecs ───────────────────────────────────────────────────────────
        if need_reencode_video:
            cmd += ["-c:v", "libx264", "-preset", video_preset, "-crf", video_crf]
            # Phải đứng SAU -preset mới ghi đè được thiết lập của preset.
            if video_x264_params:
                cmd += ["-x264-params", video_x264_params]
            cmd += ["-pix_fmt", "yuv420p", "-threads", "0"]
        else:
            cmd += ["-c:v", "copy"]

        if audio_idx is not None:
            # New audio → always re-encode to aac
            cmd += ["-c:a", "aac", "-b:a", audio_bitrate]
        elif audio_path is None:
            # Keep original audio. Re-encode to aac when we cut the start (seek
            # aligns both streams, re-encode guarantees clean sync); copy
            # otherwise (cắt đuôi bằng -t vẫn copy được).
            # Khi copy: thêm -avoid_negative_ts make_zero để tránh mất audio
            # sync với một số codec stream (Opus, DTS) trong container MP4.
            if trim_start > 0:
                cmd += ["-c:a", "aac", "-b:a", audio_bitrate]
            else:
                cmd += ["-c:a", "copy", "-avoid_negative_ts", "make_zero"]
        # mute: no -c:a needed (no audio stream)

        cmd += [output_path]

        # ── run ──────────────────────────────────────────────────────────────
        if should_stop and should_stop():
            raise RuntimeError("Đã dừng theo yêu cầu")

        # encoding utf-8: FFmpeg ghi stderr UTF-8 (đường dẫn/metadata tiếng
        # Việt); giải mã theo locale cp1252/cp1258 sẽ lỗi và mất stderr.
        # stdin=DEVNULL: nhiều FFmpeg song song không được đọc terminal.
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
            creationflags=_WIN_FLAGS,
        )
        with self._process_lock:
            self._active_processes.add(process)
        try:
            deadline = time.monotonic() + 600
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if should_stop and should_stop():
                        process.terminate()
                        try:
                            process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate()
                        raise RuntimeError("Đã dừng theo yêu cầu")
                    if time.monotonic() >= deadline:
                        process.kill()
                        stdout, stderr = process.communicate()
                        raise RuntimeError(f"FFmpeg quá thời hạn 10 phút\nStderr:\n{stderr}")
        finally:
            with self._process_lock:
                self._active_processes.discard(process)

        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg lỗi (exit code {process.returncode}):\n{stderr}")

        # Kiểm tra file output thực sự tồn tại và có dữ liệu hợp lệ.
        # Trên Windows khi nhiều FFmpeg chạy song song + I/O bão hòa,
        # returncode có thể = 0 nhưng output bị truncate/rỗng → mất tiếng.
        if not os.path.exists(output_path):
            raise RuntimeError(
                f"FFmpeg báo thành công nhưng output không tồn tại: {output_path}\n"
                f"Stderr:\n{stderr}")
        out_size = os.path.getsize(output_path)
        if out_size < 2048:   # < 2KB: chắc chắn không phải video hợp lệ
            raise RuntimeError(
                f"FFmpeg output quá nhỏ ({out_size} byte) — có thể bị cắt hoặc rỗng.\n"
                f"Stderr:\n{stderr}")

        return output_path


class VideoDownloader:
    """Downloads a video URL to a local file with retry and progress."""

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://shopee.vn/",
    }

    def __init__(self):
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        self.last_info = None
        self.session = requests.Session()
        # Retry tự động ngay ở tầng mạng/urllib3 khi gặp lỗi SSL handshake, connection reset, hoặc server lỗi
        # Vòng thử lại chính nằm trong download() (7 lần); ở tầng urllib3 chỉ
        # thử nhẹ. Retry lồng nhau 5×7 với timeout 60s từng khiến 1 link chết
        # chiếm luồng hàng chục phút.
        retries = Retry(
            total=2,
            connect=2,
            read=1,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def download(self, url: str, output_path: str,
                 progress_cb=None, retries: int = 7, log=None,
                 should_stop=None) -> str:
        """
        Download *url* to *output_path*.

        progress_cb(float 0.0-1.0) is called periodically.
        should_stop() trả True → dừng sớm, bỏ các lần thử còn lại.
        Raises on permanent failure after *retries* attempts.
        """
        # Link Google Drive dạng "xem" → link tải trực tiếp (giữ nguyên link khác).
        req_url = _to_direct_download_url(url)
        # {duration, width, height} của file vừa tải (probe_video), None nếu lỗi.
        self.last_info = None

        # Không chặn trước theo đồng hồ máy (đồng hồ sai giờ sẽ chặn nhầm cả
        # link còn hạn) — chỉ dùng mốc hết hạn để giải thích khi CDN trả 403.
        expiry = link_expiry(req_url)

        last_err = None
        for attempt in range(1, retries + 1):
            if should_stop and should_stop():
                break
            resp = None
            try:
                resp = self.session.get(
                    req_url, stream=True, headers=self.HEADERS,
                    timeout=(20, 120), allow_redirects=True
                )
                resp.raise_for_status()

                # (1) Nội dung trả về không phải video. Link share-video
                # (sv.shopee.vn/share-video/…) hoặc link sản phẩm trả HTML 200,
                # KHÔNG phải file .mp4. Thử trích link .mp4 gốc nhúng trong trang
                # rồi tải lại; không có mới báo lỗi. (Link hết hạn/chặn bot cũng
                # rơi vào đây — lưu HTML thành .mp4 sẽ "moov atom not found".)
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if _is_non_video_ctype(ctype):
                    real = _extract_direct_video_url(resp.text)
                    resp.close()
                    if not real or real == req_url:
                        raise RuntimeError(
                            "URL không trả về video "
                            f"(Content-Type: {ctype or 'rỗng'})")
                    if log:
                        log(f"   ↻ Trang HTML → dùng link gốc: {real}")
                    resp = self.session.get(
                        real, stream=True, headers=self.HEADERS,
                        timeout=(20, 120), allow_redirects=True
                    )
                    resp.raise_for_status()
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if _is_non_video_ctype(ctype):
                        raise RuntimeError(
                            "Link trích từ trang vẫn không phải video "
                            f"(Content-Type: {ctype or 'rỗng'})")

                total = int(resp.headers.get("content-length", 0))
                downloaded = 0

                with open(output_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1048576):  # 1MB/chunk
                        if should_stop and should_stop():
                            raise RuntimeError("Đã dừng theo yêu cầu")
                        if chunk:
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and total:
                                progress_cb(downloaded / total)

                # (2) Phát hiện tải thiếu byte (đứt giữa chừng): moov atom
                # thường nằm ở cuối file, thiếu đuôi = mất moov = file hỏng.
                if total and downloaded < total:
                    raise RuntimeError(
                        f"Tải thiếu dữ liệu: {downloaded}/{total} byte "
                        f"(kết nối bị đứt)")

                # (3) File quá nhỏ → gần như chắc chắn không phải video.
                if downloaded < 1024:
                    raise RuntimeError(
                        f"File tải về quá nhỏ ({downloaded} byte) — không hợp lệ")

                # (4) Kiểm tra cuối bằng ffprobe: file phải thực sự đọc được.
                # Bắt mọi loại hỏng còn lại (rác, codec lỗi, moov hỏng...).
                # Giữ kết quả probe để process_video khỏi ffprobe lần nữa.
                self.last_info = probe_video(output_path)
                if self.last_info is None:
                    raise RuntimeError(
                        "File tải về không phải video hợp lệ "
                        "(ffprobe không đọc được — moov atom/dữ liệu hỏng)")

                if progress_cb:
                    progress_cb(1.0)
                return output_path

            except Exception as exc:
                last_err = exc
                if should_stop and should_stop():
                    break
                if log:
                    log(f"! Tải lần {attempt}/{retries} lỗi: {short_reason(exc, 'DL')}")
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in _PERMANENT_HTTP_STATUS:
                    if expiry is not None and expiry < time.time():
                        last_err = RuntimeError(
                            "Link video đã hết hạn lúc "
                            f"{time.strftime('%H:%M %d/%m/%Y', time.localtime(expiry))}"
                            f" — cần lấy link mới (HTTP {status})")
                    break   # link hết hạn/bị chặn: thử lại cũng vô ích
                if attempt < retries:
                    # Chờ cố định 2s giữa các lần thử (mặc định).
                    for _ in range(2):
                        if should_stop and should_stop():
                            break
                        time.sleep(1)
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

        # Thất bại hẳn: dọn file hỏng/dở để không bị đem đi xử lý nhầm.
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass

        if should_stop and should_stop():
            raise RuntimeError("Đã dừng theo yêu cầu")
        raise RuntimeError(
            f"Tải video thất bại sau {attempt} lần thử: {last_err}"
        ) from last_err
