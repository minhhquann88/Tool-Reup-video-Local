"""Lăn chuột trong hộp chọn file/messagebox của Tk (widget thuần Tcl, Linux) không
được làm CTkScrollableFrame báo lỗi "'str' object has no attribute 'master'"."""
import sys
import tkinter
import unittest
from types import SimpleNamespace

import customtkinter as ctk

import main


class _Canvas:
    master = None   # như tkinter.Canvas: có thuộc tính master


class _FakeFrame:
    def __init__(self):
        self._parent_canvas = _Canvas()
        self._shift_pressed = False

    def _check_if_valid_scroll(self, widget):
        return ctk.CTkScrollableFrame._check_if_valid_scroll(self, widget)


class CheckValidScrollTests(unittest.TestCase):
    def test_patch_installed(self):
        fn = ctk.CTkScrollableFrame._check_if_valid_scroll
        self.assertTrue(getattr(fn, "_reup_safe", False))
        main._patch_ctk_scroll_for_tcl_widgets()  # gọi lại không bọc 2 lần
        self.assertIs(fn, ctk.CTkScrollableFrame._check_if_valid_scroll)

    def test_tcl_path_string_is_ignored(self):
        frame = _FakeFrame()
        self.assertFalse(frame._check_if_valid_scroll(
            ".__tk_filedialog.contents.f2.list"))

    def test_mouse_wheel_on_tcl_widget_does_not_raise(self):
        frame = _FakeFrame()
        event = SimpleNamespace(widget=".__tk__messagebox.msg",
                                num=4, delta=120)
        ctk.CTkScrollableFrame._mouse_wheel_all(frame, event)

    def test_python_widget_chain_still_scrolls(self):
        frame = _FakeFrame()
        child = SimpleNamespace(master=SimpleNamespace(master=frame._parent_canvas))
        self.assertTrue(frame._check_if_valid_scroll(child))
        orphan = SimpleNamespace(master=None)
        self.assertFalse(frame._check_if_valid_scroll(orphan))


class FileTypesTests(unittest.TestCase):
    def test_last_entry_is_all_files(self):
        ft = main._file_types(("Audio", "*.mp3 *.wav"))
        self.assertEqual("Audio", ft[0][0])
        self.assertIn("*.mp3", ft[0][1])
        self.assertEqual("Tất cả", ft[-1][0])
        if sys.platform == "win32":
            self.assertEqual("*.*", ft[-1][1])
        else:
            self.assertEqual("*", ft[-1][1])
            self.assertIn("*.MP3", ft[0][1])
            self.assertIn("*.WAV", ft[0][1])


class RealTkScrollTests(unittest.TestCase):
    """Sự kiện con lăn thật trên widget Tcl không có wrapper Python."""

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "Windows/macOS chuyển con lăn về cửa sổ có focus")
    def test_wheel_event_on_raw_tcl_widget(self):
        try:
            root = ctk.CTk()
        except tkinter.TclError as exc:
            self.skipTest(f"không có màn hình: {exc}")
        errors = []
        root.report_callback_exception = lambda *a: errors.append(a[1])
        try:
            ctk.CTkScrollableFrame(root).pack()
            # giống danh sách file trong tk_getOpenFile: widget Tcl, không có wrapper Python
            root.tk.eval("listbox .rawlb; pack .rawlb")
            root.update()   # phải hiện ra thì sự kiện chuột mới được giao
            root.tk.eval("event generate .rawlb <Button-4> -x 5 -y 5")
            root.tk.eval("event generate .rawlb <Button-5> -x 5 -y 5")
            root.update()
        finally:
            root.destroy()
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
