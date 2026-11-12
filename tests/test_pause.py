"""Tạm dừng / Tiếp tục: treo FFmpeg thật (tiến trình con) và giờ chạy batch."""
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock

import core
import main

# Tiến trình con in bộ đếm tăng dần ra stdout → treo thì bộ đếm đứng yên.
_COUNTER = (
    "import sys, time\n"
    "n = 0\n"
    "while True:\n"
    "    n += 1\n"
    "    print(n, flush=True)\n"
    "    time.sleep(0.01)\n"
)


class SuspendProcessTests(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen([sys.executable, "-c", _COUNTER],
                                     stdout=subprocess.PIPE, text=True,
                                     creationflags=core._WIN_FLAGS)
        self.count = 0
        self._started = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()
        self.vp = core.VideoProcessor()
        self.vp._active_processes.add(self.proc)
        # Máy bận (cả bộ test, antivirus) có thể khởi động Python con chậm.
        self.assertTrue(self._started.wait(30), "tiến trình con không chạy")

    def _pump(self):
        for line in self.proc.stdout:
            self.count = int(line)
            self._started.set()

    def _read_counter(self):
        return self.count

    def tearDown(self):
        self.vp.resume_active()
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)
        self.proc.stdout.close()

    def test_suspend_freezes_and_resume_continues(self):
        self.vp.suspend_active()
        self.assertTrue(self.vp.paused)
        time.sleep(0.2)                   # bỏ qua lần ghi đang dở
        frozen = self._read_counter()
        time.sleep(0.4)
        self.assertEqual(frozen, self._read_counter())

        self.vp.resume_active()
        self.assertFalse(self.vp.paused)
        time.sleep(0.4)
        self.assertGreater(self._read_counter(), frozen)

    def test_double_suspend_needs_single_resume(self):
        # NtSuspendProcess đếm lồng nhau: bấm Tạm dừng 2 lần không được làm
        # FFmpeg kẹt vĩnh viễn sau 1 lần Tiếp tục.
        self.vp.suspend_active()
        self.vp.suspend_active()
        self.vp.resume_active()
        before = self._read_counter()
        time.sleep(0.4)
        self.assertGreater(self._read_counter(), before)

    def test_cancel_terminates_suspended_process(self):
        self.vp.suspend_active()
        self.vp.cancel_active()
        self.assertFalse(self.vp.paused)
        self.proc.wait(timeout=5)
        self.assertIsNotNone(self.proc.returncode)


class PauseClockTests(unittest.TestCase):
    def _app(self):
        app = object.__new__(main.App)
        app._pause_event = threading.Event()
        app._cancel_event = threading.Event()
        app._paused_total = 0.0
        app._paused_since = None
        app._processing = True
        app._processor = MagicMock()
        app._btn_pause = MagicMock()
        app._log_msg = lambda *a, **k: None
        return app

    def test_paused_time_is_not_counted(self):
        app = self._app()
        app._set_paused(True)
        app._processor.suspend_active.assert_called_once()
        self.assertTrue(app._pause_event.is_set())
        app._paused_since -= 30           # giả lập tạm dừng 30 giây
        app._set_paused(False)
        app._processor.resume_active.assert_called_once()
        self.assertFalse(app._pause_event.is_set())
        self.assertAlmostEqual(30, app._paused_seconds(time.monotonic()), delta=1)

    def test_ongoing_pause_counts_until_batch_end(self):
        app = self._app()
        app._paused_total = 10.0
        app._paused_since = 100.0
        self.assertEqual(15.0, app._paused_seconds(105.0))

    def test_toggle_ignored_after_stop(self):
        app = self._app()
        app._cancel_event.set()
        app._toggle_pause()
        self.assertFalse(app._pause_event.is_set())

    def test_wait_while_paused_returns_on_stop(self):
        app = self._app()
        app._pause_event.set()
        threading.Timer(0.2, app._cancel_event.set).start()
        t0 = time.monotonic()
        app._wait_while_paused()
        self.assertLess(time.monotonic() - t0, 2)


if __name__ == "__main__":
    unittest.main()
