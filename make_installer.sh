#!/usr/bin/env bash
# Tạo MỘT file cài đặt tự giải nén: dist/VideoReupTool-installer.run
#
# Gói gồm: mã nguồn + run_linux.sh + (ffmpeg Linux nếu có sẵn). Người dùng chạy:
#     bash VideoReupTool-installer.run
# → tự giải nén vào ~/.local/share/VideoReupTool, tạo lối tắt trong menu, rồi
#   khởi động. App chạy bằng Python + Tk CỦA MÁY người dùng → KHÔNG segfault,
#   KHÔNG lỗi glibc, hợp Ubuntu cũ (>= 20.04, Python >= 3.8).
#
# NÊN chạy script này TRÊN máy Ubuntu để gói kèm ffmpeg Linux. Chạy trên Windows
# (Git Bash) cũng được nhưng sẽ KHÔNG kèm ffmpeg (installer tự tải lúc chạy đầu).
set -euo pipefail
cd "$(dirname "$0")"

APP="VideoReupTool"
OUT="dist/$APP-installer.run"
STAGE="dist/.installer_stage"
PKG="$STAGE/$APP"

FILES=(main.py core.py drive.py license.py tts.py setup_ffmpeg.py
       requirements.txt run_linux.sh client_secret.json video.png video.ico)
for f in "${FILES[@]}"; do
    [ -f "$f" ] || { echo "[ERROR] Thiếu file: $f"; exit 1; }
done

# ffmpeg Linux: nếu chưa có và đang trên Linux thì tải để gói kèm
if { [ ! -x bin/ffmpeg ] || [ ! -x bin/ffprobe ]; } && [ "$(uname -s)" = "Linux" ]; then
    echo "[*] Tải ffmpeg Linux để gói kèm..."
    python3 setup_ffmpeg.py || echo "[WARN] Không tải được ffmpeg — installer sẽ tự lo khi chạy lần đầu."
fi

echo "[*] Gom file..."
rm -rf "$STAGE"; mkdir -p "$PKG"
cp "${FILES[@]}" "$PKG"/

# Kèm ffmpeg nếu là binary Linux (ELF: 4 byte đầu chứa 'ELF')
if [ -f bin/ffmpeg ] && head -c4 bin/ffmpeg 2>/dev/null | grep -qa ELF; then
    mkdir -p "$PKG/bin"
    cp bin/ffmpeg bin/ffprobe "$PKG/bin"/ 2>/dev/null || true
    chmod +x "$PKG/bin/"* 2>/dev/null || true
    echo "[*] Đã kèm ffmpeg/ffprobe Linux vào gói."
else
    echo "[*] Không kèm ffmpeg (installer sẽ dùng ffmpeg hệ thống hoặc tự tải lúc chạy đầu)."
fi
chmod +x "$PKG/run_linux.sh" 2>/dev/null || true

echo "[*] Tạo payload..."
PAYLOAD="$STAGE/payload.tar.gz"
tar -czf "$PAYLOAD" -C "$STAGE" "$APP"

echo "[*] Ghi file .run..."
mkdir -p dist
cat > "$OUT" <<'RUNHEADER'
#!/usr/bin/env bash
# === Video Reup Tool — self-extracting installer ===
set -e
INSTALL_PARENT="${VIDEOREUP_DIR:-$HOME/.local/share}"
APP_DIR="$INSTALL_PARENT/VideoReupTool"

echo "=================================================="
echo " Cài Video Reup Tool"
echo " Thư mục: $APP_DIR"
echo "=================================================="

mkdir -p "$INSTALL_PARENT"
# Giải nén phần payload (tar.gz nối phía sau file này)
LINE=$(grep -a -n -m1 '^__VIDEOREUP_PAYLOAD_BELOW__$' "$0" | cut -d: -f1)
tail -n +$((LINE + 1)) "$0" | tar -xz -C "$INSTALL_PARENT"

# Lối tắt trong menu ứng dụng (mở bằng double-click sau này)
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/videoreuptool.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Video Reup Tool
Exec=bash -c "cd '$APP_DIR' && bash run_linux.sh"
Icon=$APP_DIR/video.png
Terminal=true
Categories=AudioVideo;
DESK
chmod +x "$APPS_DIR/videoreuptool.desktop" 2>/dev/null || true

echo
echo "Đã cài. Khởi động lần đầu (cài thư viện Python — cần mạng, vài phút)..."
echo
cd "$APP_DIR"
exec bash run_linux.sh
exit 0
__VIDEOREUP_PAYLOAD_BELOW__
RUNHEADER
cat "$PAYLOAD" >> "$OUT"
chmod +x "$OUT"

rm -rf "$STAGE"
SIZE=$(du -h "$OUT" 2>/dev/null | cut -f1 || echo "?")
echo
echo "========================================================"
echo " Xong: $OUT  ($SIZE)"
echo "========================================================"
echo " Gửi DUY NHẤT file này. Người dùng cài & chạy bằng:"
echo "     bash $APP-installer.run"
echo
echo " Yêu cầu máy người dùng (Ubuntu >= 20.04):"
echo "     sudo apt install -y python3 python3-venv python3-tk"
echo "     (nếu installer không kèm ffmpeg, cài thêm: sudo apt install -y ffmpeg)"
