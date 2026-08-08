"""Real Tk/X11 glyph stress test. Run under xvfb-run on Linux.

An X protocol error terminates the process, so reaching the final print is the
assertion that RenderAddGlyphs remained healthy under the production policy.
"""
import sys
from pathlib import Path

if not sys.platform.startswith("linux"):
    raise SystemExit("This stress test is Linux/X11-only")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import customtkinter as ctk

import main


def run() -> None:
    root = ctk.CTk()
    main._apply_linux_x11_fix(root)
    root.withdraw()
    textbox = ctk.CTkTextbox(
        root, width=900, height=500, font=(main._UI_MONO_FONT, 12)
    )
    textbox.pack()

    errors = [
        main._clean_ui_text(
            f"X [{index}/50] Lỗi Unicode: tiếng Việt — dữ liệu hỏng ⚠",
            max_len=60,
        )
        for index in range(50)
    ]
    normal = []
    textbox.insert("end", "\n".join(errors) + "\n")
    for index in range(1500):
        line = main._clean_ui_text(
            f"Tiến trình {index}/1500: tải và xử lý video → hoàn tất ✅",
            max_len=60,
        )
        normal.append(line)
        textbox.insert("end", line + "\n")
        if len(normal) > main.MAX_UI_LOGS:
            normal.pop(0)
            # Fifty preserved error rows occupy lines 1..50, so line 51 is
            # always the oldest ordinary row, matching production trimming.
            textbox.delete("51.0", "52.0")
        if index % 25 == 0:
            root.update_idletasks()

    retry_line = ""
    for attempt in range(1, 11):
        retry_line = main._clean_ui_text(
            f"[1/1] ! Tải lần {attempt}/10", max_len=60
        )
        textbox.delete("end-2l", "end-1l")
        textbox.insert("end-1l", retry_line + "\n")
        root.update()

    assert retry_line.endswith("10/10")
    assert len(normal) == main.MAX_UI_LOGS
    root.destroy()
    print("X11 RenderAddGlyphs stress test: PASS")


if __name__ == "__main__":
    run()
