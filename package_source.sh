#!/usr/bin/env bash
# Đóng gói MÃ NGUỒN để phân phối theo "Hướng B" — người dùng chạy bằng Python
# của chính máy họ (không đóng băng, nên không dính lỗi Tcl/Tk / glibc / FUSE).
#
# Tạo: dist/VideoReupTool-src.tar.gz  (chỉ gồm file cần thiết; KHÔNG kèm
#      .venv / bin / dist / build / .git / token.json / license.json).
#
# Chạy được trên cả Windows (Git Bash) lẫn Linux:
#   bash package_source.sh
set -euo pipefail
cd "$(dirname "$0")"

OUT="dist/VideoReupTool-src.tar.gz"
STAGE_ROOT="dist/.srcpkg"
STAGE="$STAGE_ROOT/VideoReupTool"

# Các file BẮT BUỘC để chạy app từ nguồn
FILES=(
    main.py
    core.py
    drive.py
    license.py
    tts.py
    setup_ffmpeg.py
    requirements.txt
    run_linux.sh
    client_secret.json
    video.png
    video.ico
)

# Kiểm tra đủ file
for f in "${FILES[@]}"; do
    [ -f "$f" ] || { echo "[ERROR] Thiếu file: $f"; exit 1; }
done

mkdir -p dist
rm -rf "$STAGE_ROOT"
mkdir -p "$STAGE"
cp "${FILES[@]}" "$STAGE"/
chmod +x "$STAGE/run_linux.sh" 2>/dev/null || true

rm -f "$OUT"
tar -czf "$OUT" -C "$STAGE_ROOT" VideoReupTool
rm -rf "$STAGE_ROOT"

echo
echo "========================================================"
echo " Xong: $OUT"
echo "========================================================"
echo " Gửi DUY NHẤT file này cho người dùng. Họ chạy:"
echo "   tar -xzf VideoReupTool-src.tar.gz"
echo "   cd VideoReupTool"
echo "   bash run_linux.sh"
