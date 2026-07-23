#!/usr/bin/env bash
# Build AppImage TRONG DOCKER trên một Ubuntu CŨ (glibc thấp) để binary chạy
# được trên máy người dùng đời cũ. Khắc phục lỗi:
#     ./VideoReupTool: /lib/.../libc.so.6: version `GLIBC_2.XX' not found
#
# Vì sao chạy được dù máy bạn là Ubuntu 24.04 (glibc 2.39):
#   Container chạy glibc RIÊNG của image (vd 22.04 = 2.35), độc lập với máy host.
#   PyInstaller build bên trong container → binary chỉ đòi glibc của image đó →
#   chạy được trên mọi máy có glibc >= mức ấy.
#
# Cách dùng:
#   bash build_appimage_docker.sh                                  # ra AppImage, ubuntu:22.04 (glibc 2.35)
#   PACKAGE=tar bash build_appimage_docker.sh                      # ra .tar.gz (bản thư mục, KHÔNG AppImage)
#   BASE_IMAGE=ubuntu:20.04 PYVER=3.11 bash build_appimage_docker.sh   # đỡ máy cũ hơn (glibc 2.31)
#
# Kết quả: dist/VideoReupTool.AppImage  (hoặc dist/VideoReupTool-linux.tar.gz nếu PACKAGE=tar)
set -euo pipefail
cd "$(dirname "$0")"

BASE_IMAGE="${BASE_IMAGE:-ubuntu:20.04}"   # bản Ubuntu để build → quyết định glibc TỐI THIỂU (20.04 = glibc 2.31)
PYVER="${PYVER:-}"                          # Dùng Python mặc định của image (20.04 = Python 3.8, tương thích tốt)

# ── Kiểm tra điều kiện ─────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo "[ERROR] Chưa có Docker. Cài:"
    echo "    sudo apt update && sudo apt install -y docker.io"
    echo "    sudo systemctl enable --now docker"
    echo "    sudo usermod -aG docker \$USER     # rồi đăng xuất/đăng nhập lại"
    exit 1
fi
if [ ! -f client_secret.json ]; then
    echo "[ERROR] Thiếu client_secret.json trong thư mục project."
    exit 1
fi

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

echo "========================================================"
echo " Build AppImage trong $BASE_IMAGE"
if [ -n "$PYVER" ]; then echo " Python: $PYVER (deadsnakes)"; else echo " Python: bản sẵn có của image"; fi
echo " → chạy được trên máy có glibc >= của $BASE_IMAGE"
echo "========================================================"

# APPIMAGE_EXTRACT_AND_RUN do build_linux.sh tự đặt → appimagetool không cần FUSE
# (Docker thường không có /dev/fuse).
docker run --rm \
    -v "$PWD":/app -w /app \
    -e DEBIAN_FRONTEND=noninteractive \
    -e PYVER="$PYVER" \
    -e PACKAGE="${PACKAGE:-}" \
    -e APP="${APP:-}" \
    -e HOST_UID="$HOST_UID" -e HOST_GID="$HOST_GID" \
    "$BASE_IMAGE" bash -c '
        set -e
        apt-get update
        apt-get install -y ca-certificates curl file binutils
        if [ -n "$PYVER" ]; then
            apt-get install -y software-properties-common
            add-apt-repository -y ppa:deadsnakes/ppa
            apt-get update
            apt-get install -y "python$PYVER" "python$PYVER-venv" "python$PYVER-tk"
            export PYTHON="python$PYVER"
        else
            apt-get install -y python3 python3-venv python3-tk python3-dev
            export PYTHON="python3"
        fi

        # Build sạch (nếu không xoá được do bị Windows lock thì bỏ qua, vì trong container ta dùng /tmp)
        rm -rf .venv build 2>/dev/null || true
        bash build_linux.sh

        # Docker chạy bằng root → trả quyền sở hữu file output về user host
        chown -R "$HOST_UID:$HOST_GID" .venv build dist bin 2>/dev/null || true
    '

echo
echo "========================================================"
echo " XONG: dist/${APP:-RenderVideoReupPro}.AppImage"
echo " (build trên $BASE_IMAGE → chạy trên glibc >= bản này)"
echo "========================================================"
