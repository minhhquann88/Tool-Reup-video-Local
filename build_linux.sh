#!/usr/bin/env bash
# Render Video Reup - Build AppImage cho Linux
# (PyInstaller --onedir  →  AppDir  →  appimagetool  →  *.AppImage)
#
# LƯU Ý:
#   - PyInstaller KHÔNG cross-compile: phải chạy script này TRÊN Linux.
#   - Để tương thích máy cũ, build trên bản Ubuntu CŨ NHẤT cần hỗ trợ
#     (hoặc dùng Docker ubuntu:20.04 — xem docs/BUILD_LINUX.md).
set -euo pipefail
cd "$(dirname "$0")"

DEFAULT_APP=$(python3 -c "import main, license; base = 'RenderVideoReupPro' if getattr(license, 'APP_ID', '') == 'tool_reup_video_pro' else 'RenderVideoReup'; print(f'{base}_{main.APP_VERSION}')" 2>/dev/null || echo "RenderVideoReup_v1.0.0")
APP="${APP:-$DEFAULT_APP}"
VERSION=$(python3 -c "import main; print(main.APP_VERSION)" 2>/dev/null || echo "v1.0.0")
case "$APP" in
    *"$VERSION"*) ;;
    *) APP="${APP}_${VERSION}" ;;
esac
ARCH="x86_64"
# Python dùng để TẠO venv. Đổi qua biến môi trường PYTHON khi cần — ví dụ build
# trong Docker Ubuntu cũ (glibc thấp) nhưng cần Python mới: PYTHON=python3.11.
# Mặc định python3 của hệ thống.
PYTHON="${PYTHON:-python3}"
# Kiểu đóng gói: "appimage" (mặc định) hoặc "tar" (thư mục onedir nén .tar.gz,
# KHÔNG dùng AppImage → không cần FUSE, mở nhanh hơn).
PACKAGE="${PACKAGE:-appimage}"

# Nếu chạy trong Docker, sử dụng thư mục tạm /tmp/venv và /tmp/build để tránh ghi đè
# hoặc gặp lỗi file lock trên thư mục mount của Windows (/app/.venv).
if [ -f /.dockerenv ]; then
    echo "[*] Đang chạy trong Docker. Sử dụng thư mục tạm để tối ưu tốc độ và tránh file lock."
    VENV_DIR="/tmp/venv"
    BUILD_DIR="/tmp/build"
    DIST_DIR="/tmp/dist"
else
    VENV_DIR=".venv"
    BUILD_DIR="build"
    DIST_DIR="dist"
fi
VENV_PY="$VENV_DIR/bin/python3"

echo "========================================"
echo " $APP - Build AppImage (Linux)"
echo "========================================"

# ── client_secret.json ────────────────────────────────────────────────────────
if [ ! -f "client_secret.json" ]; then
    echo "[ERROR] Không tìm thấy client_secret.json. Đặt file này vào thư mục project."
    exit 1
fi

# ── Tkinter (cần cho GUI) ─────────────────────────────────────────────────────
if ! "$PYTHON" -c "import tkinter" >/dev/null 2>&1; then
    echo "[ERROR] Thiếu Tkinter cho $PYTHON. Cài: sudo apt install python3-tk (hoặc ${PYTHON}-tk)"
    exit 1
fi

# ── Virtualenv CHẮC CHẮN có pip ───────────────────────────────────────────────
# Sửa lỗi "externally-managed-environment" (Ubuntu 23.04+/Debian 12+, PEP 668):
# luôn gọi pip QUA python của venv, và tạo lại venv nếu thiếu/hỏng/là venv của
# OS khác (vd .venv copy nhầm từ Windows sang).
if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    echo "[*] Tạo (lại) virtualenv ($VENV_DIR) bằng $PYTHON..."
    rm -rf "$VENV_DIR"
    if ! "$PYTHON" -m venv "$VENV_DIR"; then
        echo "[ERROR] Không tạo được venv bằng $PYTHON. Cài: sudo apt install ${PYTHON}-venv python3-full"
        exit 1
    fi
fi
# Một số bản venv tạo thiếu pip → bơm pip vào
if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    "$VENV_PY" -m ensurepip --upgrade || {
        echo "[ERROR] Thiếu pip trong venv. Cài: sudo apt install python3-venv python3-full"
        exit 1
    }
fi

echo
echo "[1/4] Cài thư viện (dùng pip CỦA venv, không đụng Python hệ thống)..."
"$VENV_PY" -m pip install --upgrade pip -q
"$VENV_PY" -m pip install -r requirements.txt -q
"$VENV_PY" -m pip install pyinstaller -q

# ── FFmpeg Linux trong bin/ (để bundle kèm) ───────────────────────────────────
if [ ! -x "bin/ffmpeg" ]; then
    echo "[*] Chưa có bin/ffmpeg (Linux) - tải qua setup_ffmpeg.py..."
    "$VENV_PY" setup_ffmpeg.py || { echo "[ERROR] Không tải được FFmpeg."; exit 1; }
fi

# ── PyInstaller (onedir) ──────────────────────────────────────────────────────
echo
echo "[2/4] Đóng gói PyInstaller..."
rm -rf "$BUILD_DIR/$APP" "$DIST_DIR/$APP" "dist/$APP" "$APP.spec" 2>/dev/null || true
mkdir -p "$DIST_DIR" "dist"
"$VENV_PY" -m PyInstaller --noconfirm --onedir \
    --workpath "$BUILD_DIR" \
    --distpath "$DIST_DIR" \
    --name "$APP" \
    --add-data "client_secret.json:." \
    --add-data "bin:bin" \
    --collect-all customtkinter \
    --hidden-import "PIL._tkinter_finder" \
    --hidden-import "google.auth.transport.requests" \
    --hidden-import "google.oauth2.credentials" \
    --hidden-import "google_auth_oauthlib.flow" \
    --hidden-import "googleapiclient.discovery" \
    --hidden-import "googleapiclient.http" \
    --hidden-import "googleapiclient._helpers" \
    --hidden-import "openpyxl" \
    main.py

# Không để token/license lọt vào gói
rm -f "$DIST_DIR/$APP/token.json" "$DIST_DIR/$APP/license.json" 2>/dev/null || true

# ── Quyền thực thi cho binary chính + ffmpeg/ffprobe ──────────────────────────
chmod +x "$DIST_DIR/$APP/$APP" 2>/dev/null || true
find "$DIST_DIR/$APP" -type f \( -name ffmpeg -o -name ffprobe \) \
    -exec chmod +x {} + 2>/dev/null || true

# ── Chế độ "tar": dừng ở bản thư mục onedir, nén .tar.gz (KHÔNG AppImage) ──────
if [ "$PACKAGE" = "tar" ] || [ "$PACKAGE" = "dir" ]; then
    echo
    echo "[3/3] Nén gói .tar.gz (bản thư mục, không dùng AppImage)..."
    OUT_TAR="dist/$APP-linux.tar.gz"
    rm -f "$OUT_TAR"
    tar -czf "$OUT_TAR" -C "$DIST_DIR" "$APP"
    echo
    echo "========================================"
    echo " HOÀN THÀNH (bản thư mục, KHÔNG AppImage)!"
    echo "   Gói phân phối: $OUT_TAR"
    echo "========================================"
    echo " Người dùng giải nén & chạy:"
    echo "   tar -xzf $APP-linux.tar.gz"
    echo "   cd $APP && ./$APP"
    echo
    echo " LƯU Ý: phân phối bằng .tar.gz để GIỮ quyền +x. Đừng nén .zip qua"
    echo "        Windows/Zalo (mất +x → máy đích báo Permission denied)."
    exit 0
fi

# ── Dựng AppDir ───────────────────────────────────────────────────────────────
echo
echo "[3/4] Dựng AppDir..."
APPDIR="$BUILD_DIR/$APP.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -a "$DIST_DIR/$APP/." "$APPDIR/usr/bin/"

# Quyền thực thi cho binary chính + ffmpeg/ffprobe (dù nằm ở đâu trong gói)
chmod +x "$APPDIR/usr/bin/$APP"
find "$APPDIR/usr/bin" -type f \( -name ffmpeg -o -name ffprobe \) \
    -exec chmod +x {} + 2>/dev/null || true

# Icon (AppImage cần PNG/SVG — KHÔNG dùng .ico)
cp video.png "$APPDIR/$APP.png"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp video.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP.png"

# .desktop (Icon/Exec phải khớp tên $APP)
APP_TITLE=$(python3 -c "import main; print(main.APP_TITLE)" 2>/dev/null || echo "Render Video Reup")
cat > "$APPDIR/$APP.desktop" <<EOF
[Desktop Entry]
Type=Application
<<<<<<< HEAD
Name=Render Video Reup
=======
Name=$APP_TITLE
>>>>>>> Byscom
Comment=Tool reup video
Exec=$APP
Icon=$APP
Categories=AudioVideo;
Terminal=false
EOF
mkdir -p "$APPDIR/usr/share/applications"
cp "$APPDIR/$APP.desktop" "$APPDIR/usr/share/applications/"

cat > "$APPDIR/AppRun" <<EOF
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"

# ── Fix: X Error BadLength - RenderAddGlyphs ──────────────────────────────────
# Ép DPI = 96 (scaling = 1.0) & 1-bit monochrome glyphs để tránh bão bitmap font làm tràn X11 socket (BadLength)
export XFT_ANTIALIAS=0
export XFT_HINTING=0
export XFT_RGBA=none
export XFT_MAX_GLYPH_MEMORY=10485760
export XLIB_SKIP_ARGB_VISUALS=1
export GDK_SCALE=1
export GDK_DPI_SCALE=1
export QT_SCALE_FACTOR=1
export QT_AUTO_SCREEN_SCALE_FACTOR=0
export TK_SCALING=1
export WAYLAND_DISPLAY=

# Set Xft.dpi = 96 vào X resources database nếu có lệnh xrdb
echo "Xft.dpi: 96" | xrdb -merge 2>/dev/null || true
echo "Xft.antialias: 0" | xrdb -merge 2>/dev/null || true
# ─────────────────────────────────────────────────────────────────────────────

exec "\$HERE/usr/bin/$APP" "\$@"
EOF
chmod +x "$APPDIR/AppRun"

# ── appimagetool + runtime FUSE-less → AppImage ───────────────────────────────
echo
echo "[4/4] Tạo AppImage..."

# Hàm tải file (curl hoặc wget)
dl() {   # dl <url> <dest>
    if command -v curl >/dev/null 2>&1; then
        curl -fSL -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$2" "$1"
    else
        echo "[ERROR] Cần curl hoặc wget. Cài: sudo apt install curl"; exit 1
    fi
}

TOOL="build/appimagetool-x86_64.AppImage"
mkdir -p "$(dirname "$TOOL")"
if [ ! -x "$TOOL" ]; then
    echo "[*] Tải appimagetool..."
    dl "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" "$TOOL"
    chmod +x "$TOOL"
fi


# Runtime kiểu MỚI (type2-runtime): KHÔNG cần libfuse2; nếu máy không có FUSE thì
# tự giải nén ra /tmp rồi chạy. Đây là chìa khóa để "double-click là mở" trên
# Ubuntu 22.04/24.04 (vốn không cài sẵn libfuse2).
RUNTIME="build/runtime-x86_64"
if [ ! -f "$RUNTIME" ]; then
    echo "[*] Tải runtime FUSE-less..."
    dl "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64" "$RUNTIME"
fi

OUT="dist/$APP.AppImage"
rm -f "$OUT"
# APPIMAGE_EXTRACT_AND_RUN=1 → appimagetool tự chạy không cần FUSE trên máy build
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --runtime-file "$RUNTIME" "$APPDIR" "$OUT"
chmod +x "$OUT"

echo
echo "========================================"
echo " HOÀN THÀNH!"
echo "   File phân phối: $OUT"
echo "========================================"
echo " Runtime FUSE-less → người dùng KHÔNG cần cài libfuse2."
echo " Chỉ gửi DUY NHẤT file $APP.AppImage cho người dùng."
echo
echo " Để DOUBLE-CLICK là mở, file phải còn 'quyền thực thi' (+x):"
echo "   - Gửi cách GIỮ +x (USB ext4 / scp / rsync / .tar.gz) → double-click chạy luôn."
echo "   - Nếu tải qua trình duyệt/Drive/Zalo (mất +x), người dùng làm 1 LẦN:"
echo "       chuột phải file → Properties → Permissions →"
echo "       tick 'Allow executing file as program' → xong, double-click là mở."
