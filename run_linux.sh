#!/usr/bin/env bash
# Video Reup Tool - Setup & Launch (Linux)
# Tương đương run.bat cho Ubuntu/Debian.
set -euo pipefail
cd "$(dirname "$0")"

echo "========================================"
echo " Video Reup Tool - Setup & Launch"
echo "========================================"

# ── [0] Kiểm tra Python 3 ─────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] Chưa có python3. Cài: sudo apt install python3 python3-venv"
    exit 1
fi

# ── Kiểm tra Python >= 3.10 (code dùng cú pháp 'str | None') ───────────────────
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "[ERROR] Cần Python >= 3.10 (Ubuntu 22.04 trở lên)."
    echo "        Phiên bản hiện tại: $(python3 -V 2>&1)"
    exit 1
fi

# ── Kiểm tra Tkinter (python3-tk) ─────────────────────────────────────────────
if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "[ERROR] Thiếu Tkinter. Cài: sudo apt install python3-tk"
    exit 1
fi

# ── [1] Virtualenv + dependencies ─────────────────────────────────────────────
VENV_PY=".venv/bin/python3"

# Tạo lại nếu .venv thiếu hoặc hỏng (không có python hoặc không có pip)
if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    echo
    echo "[1/3] Tạo (lại) virtualenv (.venv)..."
    rm -rf .venv
    if ! python3 -m venv .venv; then
        echo "[ERROR] Không tạo được venv. Cài: sudo apt install python3-venv"
        exit 1
    fi
fi

# Bảo đảm có pip trong venv (một số bản venv tạo thiếu pip)
if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    "$VENV_PY" -m ensurepip --upgrade || {
        echo "[ERROR] Thiếu pip trong venv. Cài: sudo apt install python3-venv"
        exit 1
    }
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo
echo "[1/3] Kiểm tra Python dependencies..."
python3 -m pip install --upgrade pip -q
python3 -m pip install -r requirements.txt -q

# ── [2] FFmpeg ────────────────────────────────────────────────────────────────
echo
if [ -x "bin/ffmpeg" ]; then
    echo "[2/3] FFmpeg đã có trong bin/ - bỏ qua."
elif command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    echo "[2/3] Dùng FFmpeg hệ thống ($(command -v ffmpeg))."
else
    echo "[2/3] FFmpeg chưa có - đang tải vào bin/ ..."
    if ! python3 setup_ffmpeg.py; then
        echo "[ERROR] Tải FFmpeg thất bại. Cài thủ công: sudo apt install ffmpeg"
        exit 1
    fi
fi

# ── [3] Khởi động app ─────────────────────────────────────────────────────────
echo
echo "[3/3] Khởi động app..."

# Fix: X Error BadLength - RenderAddGlyphs
# 1-bit monochrome glyphs (XFT_ANTIALIAS=0) giảm 97% kích thước bitmap glyphs.
export XFT_ANTIALIAS=0
export XFT_MAX_GLYPH_MEMORY=10485760
export XFT_RGBA=none
export XFT_HINTING=0
export XLIB_SKIP_ARGB_VISUALS=1
export TK_SCALING=1
export WAYLAND_DISPLAY=

python3 main.py
