"""
setup_ffmpeg.py
Tải ffmpeg + ffprobe (BtbN builds) vào thư mục bin/.
- Windows: ffmpeg.exe / ffprobe.exe  (.zip)
- Linux  : ffmpeg / ffprobe          (.tar.xz)
Chạy 1 lần; run.bat / build_linux.sh tự gọi khi cần.
"""

import io
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# Force UTF-8 output so Vietnamese text works in cmd.exe (cp1252 default)
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if isinstance(sys.stderr, io.TextIOWrapper):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# BtbN's official nightly builds – GPL, 64-bit (Windows)
# John Van Sickle's fully static release builds (Linux - glibc-independent)
_BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"

if sys.platform == "win32":
    URL = _BASE + "ffmpeg-master-latest-win64-gpl.zip"
    NEEDED = {"ffmpeg.exe", "ffprobe.exe"}
    _KIND = "zip"
elif sys.platform == "darwin":
    # BtbN không build macOS — dùng Homebrew thay thế.
    URL = None
    NEEDED = {"ffmpeg", "ffprobe"}
    _KIND = None
else:  # linux
    URL = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    NEEDED = {"ffmpeg", "ffprobe"}
    _KIND = "tarxz"

BIN_DIR = Path(__file__).parent / "bin"


def _download(url: str) -> bytes:
    print(f"Đang tải FFmpeg từ GitHub…\n  {url}\n")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        buf = bytearray()
        while True:
            chunk = resp.read(131072)   # 128 KB chunks
            if not chunk:
                break
            buf.extend(chunk)
            if total:
                pct = len(buf) / total * 100
                mb = len(buf) / 1_048_576
                print(
                    f"\r  {pct:5.1f}%  ({mb:.1f} MB / {total/1_048_576:.1f} MB)   ",
                    end="", flush=True,
                )
    print("\n")
    return bytes(buf)


def _extract_zip(raw: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            fname = Path(info.filename).name
            if fname in NEEDED:
                data = zf.read(info.filename)
                dest = BIN_DIR / fname
                dest.write_bytes(data)
                print(f"  ✅ {fname}  ({len(data)//1024:,} KB)")


def _extract_tarxz(raw: bytes) -> None:
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:xz") as tf:
        for member in tf.getmembers():
            fname = Path(member.name).name
            if member.isfile() and fname in NEEDED:
                data = tf.extractfile(member).read()
                dest = BIN_DIR / fname
                dest.write_bytes(data)
                dest.chmod(0o755)   # cấp quyền thực thi trên Linux
                print(f"  ✅ {fname}  ({len(data)//1024:,} KB)")


def setup() -> bool:
    BIN_DIR.mkdir(exist_ok=True)

    missing = {n for n in NEEDED if not (BIN_DIR / n).exists()}
    if not missing:
        print("✅ FFmpeg đã có sẵn trong bin/ – bỏ qua tải.")
        return True

    if URL is None:
        print("⚠️ Trên macOS hãy cài FFmpeg bằng Homebrew:\n  brew install ffmpeg")
        print("App sẽ tự dùng ffmpeg/ffprobe trong PATH.")
        return False

    print(f"Cần tải: {', '.join(missing)}\n")

    try:
        raw = _download(URL)

        print("Đang giải nén…")
        if _KIND == "zip":
            _extract_zip(raw)
        else:
            _extract_tarxz(raw)

        still_missing = {n for n in NEEDED if not (BIN_DIR / n).exists()}
        if still_missing:
            print(f"\n❌ Vẫn thiếu: {still_missing}")
            return False

        print("\n✅ FFmpeg cài xong vào bin/")
        return True

    except Exception as exc:
        print(f"\n❌ Lỗi: {exc}")
        if sys.platform == "win32":
            print("\nThử cài thủ công:\n  winget install Gyan.FFmpeg")
        else:
            print("\nThử cài thủ công:\n  sudo apt install ffmpeg")
        return False


if __name__ == "__main__":
    ok = setup()
    sys.exit(0 if ok else 1)
