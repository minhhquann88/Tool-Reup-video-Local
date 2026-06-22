"""
setup_ffmpeg.py
Tải ffmpeg.exe + ffprobe.exe từ GitHub vào thư mục bin/.
Chạy 1 lần, run.bat tự gọi khi cần.
"""

import io
import sys
import urllib.request
import zipfile

# Force UTF-8 output so Vietnamese text works in cmd.exe (cp1252 default)
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if isinstance(sys.stderr, io.TextIOWrapper):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

# BtbN's official nightly build – GPL, Windows 64-bit
URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)

BIN_DIR = Path(__file__).parent / "bin"
NEEDED  = {"ffmpeg.exe", "ffprobe.exe"}


def _download(url: str) -> bytes:
    print(f"Đang tải FFmpeg từ GitHub…\n  {url}\n")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        buf   = bytearray()
        while True:
            chunk = resp.read(131072)   # 128 KB chunks
            if not chunk:
                break
            buf.extend(chunk)
            if total:
                pct = len(buf) / total * 100
                mb  = len(buf) / 1_048_576
                print(
                    f"\r  {pct:5.1f}%  ({mb:.1f} MB / {total/1_048_576:.1f} MB)   ",
                    end="", flush=True,
                )
    print("\n")
    return bytes(buf)


def setup() -> bool:
    BIN_DIR.mkdir(exist_ok=True)

    missing = {n for n in NEEDED if not (BIN_DIR / n).exists()}
    if not missing:
        print("✅ FFmpeg đã có sẵn trong bin/ – bỏ qua tải.")
        return True

    print(f"Cần tải: {', '.join(missing)}\n")

    try:
        raw = _download(URL)

        print("Đang giải nén…")
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for info in zf.infolist():
                fname = Path(info.filename).name
                if fname in NEEDED:
                    data = zf.read(info.filename)
                    dest = BIN_DIR / fname
                    dest.write_bytes(data)
                    print(f"  ✅ {fname}  ({len(data)//1024:,} KB)")

        still_missing = {n for n in NEEDED if not (BIN_DIR / n).exists()}
        if still_missing:
            print(f"\n❌ Vẫn thiếu: {still_missing}")
            return False

        print("\n✅ FFmpeg cài xong vào bin/")
        return True

    except Exception as exc:
        print(f"\n❌ Lỗi: {exc}")
        print("\nThử cài thủ công:\n  winget install Gyan.FFmpeg")
        return False


if __name__ == "__main__":
    ok = setup()
    sys.exit(0 if ok else 1)
