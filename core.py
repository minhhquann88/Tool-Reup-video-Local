"""
core.py - VideoProcessor (FFmpeg) + VideoDownloader
"""

from __future__ import annotations

import json

import os
import subprocess
import sys
import time
from pathlib import Path


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
        Return {duration: float, width: int}.
        duration is 0.0 if it can't be determined (some streamed sources
        report "N/A"); callers must handle the unknown-duration case.
        """
        cmd = [
            FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration:stream=width,duration",
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

        width = int(s0.get("width") or 0) or 1280

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

        return {"duration": duration, "width": width}

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
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-pix_fmt", "yuv420p"]
        else:
            cmd += ["-c:v", "copy"]

        if audio_idx is not None:
            # New audio → always re-encode to aac
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        elif audio_path is None:
            # Keep original audio. Re-encode to aac when we trimmed (seek aligns
            # both streams, re-encode guarantees clean sync); copy otherwise.
            if has_trim:
                cmd += ["-c:a", "aac", "-b:a", "128k"]
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
                 progress_cb=None, retries: int = 3) -> str:
        """
        Download *url* to *output_path*.

        progress_cb(float 0.0-1.0) is called periodically.
        Raises on permanent failure after *retries* attempts.
        """
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(
                    url, stream=True, headers=self.HEADERS,
                    timeout=30, allow_redirects=True
                )
                resp.raise_for_status()

                total = int(resp.headers.get("content-length", 0))
                downloaded = 0

                with open(output_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and total:
                                progress_cb(downloaded / total)

                if progress_cb:
                    progress_cb(1.0)
                return output_path

            except Exception as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(2 * attempt)   # back-off: 2s, 4s

        raise RuntimeError(
            f"Tải video thất bại sau {retries} lần thử: {last_err}"
        )
