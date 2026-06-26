"""
core.py - VideoProcessor (FFmpeg) + VideoDownloader
"""

from __future__ import annotations

import json

import os
import re
import subprocess
import sys
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


def _has_valid_video_stream(path: str) -> bool:
    """
    True nếu *path* là video đọc được (có ít nhất 1 luồng video và ffprobe
    parse được). Bắt mọi file hỏng: tải thiếu byte (mất moov atom), trang
    HTML lỗi lưu nhầm .mp4, dữ liệu rác... ffprobe sẽ trả mã != 0 hoặc
    không thấy luồng video.
    """
    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=_WIN_FLAGS)
    except Exception:   # noqa: BLE001
        return False
    return r.returncode == 0 and "video" in (r.stdout or "")


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

    # ── public ──────────────────────────────────────────────────────────────

    def get_video_info(self, video_path: str) -> dict:
        """
        Return {duration: float, width: int, height: int}.
        duration is 0.0 if it can't be determined (some streamed sources
        report "N/A"); callers must handle the unknown-duration case.
        """
        cmd = [
            FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration:stream=width,height,duration",
            "-of", "json",
            video_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=_WIN_FLAGS)
        if r.returncode != 0:
            raise RuntimeError(f"ffprobe error: {r.stderr}")

        data    = json.loads(r.stdout)
        streams = data.get("streams") or [{}]
        s0      = streams[0]

        width  = int(s0.get("width") or 0) or 1280
        height = int(s0.get("height") or 0)

        # Prefer container duration, fall back to stream duration
        def _to_float(v):
            if v in (None, "N/A", ""):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        duration = (_to_float(data.get("format", {}).get("duration"))
                    or _to_float(s0.get("duration"))
                    or 0.0)

        return {"duration": duration, "width": width, "height": height}

    def process_video(self, input_path: str, output_path: str,
                      settings: dict) -> str:
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

        # ── Chất lượng encode (giữ nguyên chất lượng video gốc) ───────────────
        # Khi buộc phải re-encode (có logo / có cắt), các tham số này quyết định
        # mức suy giảm. Mặc định đặt ở ngưỡng "gần như không thấy mất mát":
        #   crf 18   : visually lossless cho x264 (thấp hơn = đẹp hơn, file to hơn)
        #   medium   : nén hiệu quả hơn 'fast' → bù lại dung lượng khi crf thấp
        #   192k aac : audio rõ. Audio gốc không cắt vẫn được copy nguyên vẹn.
        # Có thể override qua settings nếu muốn ưu tiên tốc độ/dung lượng.
        video_crf     = str(settings.get("video_crf", 18))
        video_preset  = str(settings.get("video_preset", "medium"))
        audio_bitrate = str(settings.get("audio_bitrate", "192k"))

        info         = self.get_video_info(input_path)
        duration     = info["duration"]          # 0.0 if unknown
        video_width  = info["width"]
        logo_px      = max(1, round(video_width * logo_pct / 100))

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
        if has_trim:
            new_duration = duration - trim_start - trim_end
            if new_duration <= 0:
                raise ValueError(
                    f"Trim quá dài! Video chỉ có {duration:.1f}s, "
                    f"trim_start={trim_start}s trim_end={trim_end}s"
                )
        elif has_new_audio:
            # No trim, but cap output to the video length so longer audio
            # gets cut and shorter audio leaves a silent tail.
            new_duration = duration

        # Re-encode is REQUIRED whenever we trim, because -c:v copy can only
        # cut at keyframes → trimming mid-GOP causes freeze/stutter. Logo also
        # forces a re-encode. Only pure remux (no trim, no logo) can copy video.
        need_reencode_video = has_logo or has_trim

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
            # Logo chain: scale, then apply opacity only when not fully opaque.
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

            fc = f"{logo_chain};[0:v][logo]{overlay}[vout]"
            cmd += ["-filter_complex", fc, "-map", "[vout]"]
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
        if new_duration is not None:
            cmd += ["-t", str(new_duration)]

        # ── codecs ───────────────────────────────────────────────────────────
        if need_reencode_video:
            cmd += ["-c:v", "libx264", "-preset", video_preset,
                    "-crf", video_crf, "-pix_fmt", "yuv420p"]
        else:
            cmd += ["-c:v", "copy"]

        if audio_idx is not None:
            # New audio → always re-encode to aac
            cmd += ["-c:a", "aac", "-b:a", audio_bitrate]
        elif audio_path is None:
            # Keep original audio. Re-encode to aac when we trimmed (seek aligns
            # both streams, re-encode guarantees clean sync); copy otherwise.
            if has_trim:
                cmd += ["-c:a", "aac", "-b:a", audio_bitrate]
            else:
                cmd += ["-c:a", "copy"]
        # mute: no -c:a needed (no audio stream)

        cmd += [output_path]

        # ── run ──────────────────────────────────────────────────────────────
        r = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=_WIN_FLAGS)
        if r.returncode != 0:
            # Return last 600 chars of stderr for diagnosis
            raise RuntimeError(f"FFmpeg lỗi:\n{r.stderr[-600:]}")

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

    def download(self, url: str, output_path: str,
                 progress_cb=None, retries: int = 10, log=None,
                 should_stop=None) -> str:
        """
        Download *url* to *output_path*.

        progress_cb(float 0.0-1.0) is called periodically.
        should_stop() trả True → dừng sớm, bỏ các lần thử còn lại.
        Raises on permanent failure after *retries* attempts.
        """
        # Link Google Drive dạng "xem" → link tải trực tiếp (giữ nguyên link khác).
        req_url = _to_direct_download_url(url)

        last_err = None
        for attempt in range(1, retries + 1):
            if should_stop and should_stop():
                break
            try:
                resp = requests.get(
                    req_url, stream=True, headers=self.HEADERS,
                    timeout=(30, 30), allow_redirects=True
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
                        log(f"   ↻ Trang HTML → dùng link gốc: ...{real[-46:]}")
                    resp = requests.get(
                        real, stream=True, headers=self.HEADERS,
                        timeout=(30, 30), allow_redirects=True
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
                    for chunk in resp.iter_content(chunk_size=65536):
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
                if not _has_valid_video_stream(output_path):
                    raise RuntimeError(
                        "File tải về không phải video hợp lệ "
                        "(ffprobe không đọc được — moov atom/dữ liệu hỏng)")

                if progress_cb:
                    progress_cb(1.0)
                return output_path

            except Exception as exc:
                last_err = exc
                if attempt < retries:
                    # Chờ cố định 5s giữa các lần (giống các API), thoát sớm nếu
                    # bấm Dừng để khỏi treo lâu khi đã có 10 lần thử.
                    for _ in range(5):
                        if should_stop and should_stop():
                            break
                        time.sleep(1)

        # Thất bại hẳn: dọn file hỏng/dở để không bị đem đi xử lý nhầm.
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass

        raise RuntimeError(
            f"Tải video thất bại sau {retries} lần thử: {last_err}"
        )
