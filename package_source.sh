#!/usr/bin/env bash
# Đóng gói MÃ NGUỒN để phân phối theo "Hướng B" — người dùng chạy bằng Python
# của chính máy họ (không đóng băng, nên không dính lỗi Tcl/Tk / glibc / FUSE).
#
# Tạo: dist/<Tên bản>-src.tar.gz  (chỉ gồm file cần thiết; KHÔNG kèm
#      .venv / bin / dist / build / .git / token.json / license.json).
#      Gói kèm _edition_stamp.py để chạy đúng bản đã chọn.
#
# Chạy được trên cả Windows (Git Bash) lẫn Linux:
#   bash package_source.sh                  # bản Pro
#   EDITION=khach bash package_source.sh    # bản thường
set -euo pipefail
cd "$(dirname "$0")"

export REUP_EDITION="${EDITION:-${REUP_EDITION:-pro}}"
# Python chạy được thật (trên Windows "python3" có thể là lối tắt Microsoft Store).
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for c in python3 python; do
        if "$c" -c "" >/dev/null 2>&1; then PY="$c"; break; fi
    done
fi
[ -n "$PY" ] || { echo "[ERROR] Không tìm thấy Python."; exit 1; }
APP="$("$PY" -c "import edition; print(edition.BASE_NAME)")"
OUT="dist/$APP-src.tar.gz"
STAGE_ROOT="dist/.srcpkg"
STAGE="$STAGE_ROOT/$APP"

# Các file BẮT BUỘC để chạy app từ nguồn
FILES=(
    main.py
    core.py
    drive.py
    license.py
    app_paths.py
    edition.py
    tts.py
    updater.py
    packaging_smoke.py
    single_instance.py
    error_text.py
    linux-fontconfig.conf
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
"$PY" -c "import edition, sys; edition.write_stamp(sys.argv[1], sys.argv[2])" "$STAGE" "$REUP_EDITION"
chmod +x "$STAGE/run_linux.sh" 2>/dev/null || true

rm -f "$OUT"
tar -czf "$OUT" -C "$STAGE_ROOT" "$APP"
rm -rf "$STAGE_ROOT"

echo
echo "========================================================"
echo " Xong: $OUT"
echo "========================================================"
echo " Gửi DUY NHẤT file này cho người dùng. Họ chạy:"
echo "   tar -xzf $APP-src.tar.gz"
echo "   cd $APP"
echo "   bash run_linux.sh"
