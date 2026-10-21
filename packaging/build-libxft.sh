#!/usr/bin/env bash
# Build a known-good libXft for the Linux bundle. Tk's RenderAddGlyphs crash is
# fixed in libXft >= 2.3.5; we build 2.3.8 on the old Ubuntu base so the final
# binary keeps its glibc 2.35 compatibility.
set -euo pipefail

PREFIX="${1:?usage: build-libxft.sh PREFIX WORK_DIR}"
WORK_DIR="${2:?usage: build-libxft.sh PREFIX WORK_DIR}"
VERSION="${XFT_VERSION:-2.3.8}"
# configure bắt buộc --prefix tuyệt đối → chuẩn hóa cả hai đường dẫn.
mkdir -p "$PREFIX" "$WORK_DIR"
PREFIX="$(cd "$PREFIX" && pwd)"
WORK_DIR="$(cd "$WORK_DIR" && pwd)"
ARCHIVE="$WORK_DIR/libXft-$VERSION.tar.xz"
SOURCE_DIR="$WORK_DIR/libXft-$VERSION"
OUTPUT="$PREFIX/lib/libXft.so.2"
# libtool cài libXft.so.2 → libXft.so.<VERSION>: tên file thật là bằng chứng phiên bản.
REAL_LIB="$PREFIX/lib/libXft.so.$VERSION"

if [ -f "$OUTPUT" ] && [ -f "$REAL_LIB" ]; then
    echo "[*] libXft $VERSION đã có: $OUTPUT"
    exit 0
fi

for command_name in curl tar make pkg-config; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "[ERROR] Thiếu $command_name để build libXft $VERSION." >&2
        exit 1
    fi
done
if ! pkg-config --exists fontconfig freetype2 xrender; then
    echo "[ERROR] Thiếu thư viện build libXft." >&2
    echo "        Ubuntu/Debian: sudo apt install build-essential pkg-config libfontconfig1-dev libfreetype6-dev libxrender-dev" >&2
    exit 1
fi

if [ ! -f "$ARCHIVE" ]; then
    echo "[*] Tải libXft $VERSION từ X.Org..."
    curl -fL --retry 3 -o "$ARCHIVE" \
        "https://www.x.org/releases/individual/lib/libXft-$VERSION.tar.xz"
fi

rm -rf "$SOURCE_DIR" "$PREFIX"
tar -xJf "$ARCHIVE" -C "$WORK_DIR"

echo "[*] Build libXft $VERSION..."
(
    cd "$SOURCE_DIR"
    ./configure --prefix="$PREFIX" --disable-static
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
    make install
)

if [ ! -f "$OUTPUT" ] || [ ! -f "$REAL_LIB" ]; then
    echo "[ERROR] Không xác minh được libXft $VERSION sau khi build." >&2
    exit 1
fi
echo "[OK] libXft $VERSION: $OUTPUT"
