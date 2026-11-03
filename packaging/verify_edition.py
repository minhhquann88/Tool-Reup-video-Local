"""Kiểm tra file chạy PyInstaller đã đóng cứng đúng bản (Pro/thường).

Đọc `_edition_stamp` trong PYZ của file build — không cần chạy app hay màn hình,
dùng được cho cả EXE Windows lẫn binary Linux (onefile/onedir).

    python packaging/verify_edition.py <file chạy> <pro|khach>
"""
from __future__ import annotations

import sys


def stamped_edition(executable: str) -> str | None:
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(executable)
    for name in archive.toc:
        if name.endswith(".pyz"):
            pyz = archive.open_embedded_archive(name)
            if "_edition_stamp" not in pyz.toc:
                return None
            namespace = {}
            exec(pyz.extract("_edition_stamp"), namespace)
            return namespace.get("EDITION")
    return None


def main(argv) -> int:
    if hasattr(sys.stdout, "reconfigure"):      # console Windows (cp1252) không in được tiếng Việt
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) != 3:
        print(__doc__)
        return 2
    found = stamped_edition(argv[1])
    if found != argv[2]:
        print(f"[ERROR] {argv[1]} đóng cứng bản {found!r}, cần {argv[2]!r}")
        return 1
    print(f"[OK] {argv[1]} là bản {found}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
