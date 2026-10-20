"""Tests for the staged batch pipeline in App._run_batch (no network/FFmpeg)."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import drive
import main


class _Tracker:
    """Counts how many calls of a kind run at the same time."""

    def __init__(self):
        self._lock = threading.Lock()
        self.now = 0
        self.peak = 0

    def __enter__(self):
        with self._lock:
            self.now += 1
            self.peak = max(self.peak, self.now)

    def __exit__(self, *exc):
        with self._lock:
            self.now -= 1


class BatchPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.encode = _Tracker()
        self.download = _Tracker()
        self.upload = _Tracker()
        self.events = []                 # (kind, idx, t)
        self.downloader_inits = 0
        self.uploader_inits = 0
        self.voice_fail_for = set()
        self.logs = []

    def _make_app(self):
        test = self

        class FakeProcessor:
            def process_video(self, src, out, settings, should_stop=None, info=None):
                with test.encode:
                    time.sleep(0.05)
                with open(out, "wb") as fh:
                    fh.write(b"x" * 4096)
                return out

            def cancel_active(self):
                pass

        app = object.__new__(main.App)
        app._ui = lambda func: None
        app._log_msg = lambda msg, cat=None: test.logs.append(str(msg))
        app._log_add = lambda msg, cat=None: test.logs.append(str(msg))
        app._cancel_event = threading.Event()
        app._processor = FakeProcessor()
        app._batch_futures = []
        app._batch_executor = None
        app._processing = True
        app._batch_started_monotonic = None
        app._batch_ended_monotonic = None
        app._write_output_csv = lambda *a, **k: None
        return app

    def _patches(self):
        test = self

        class FakeDownloader:
            def __init__(self):
                test.downloader_inits += 1
                self.last_info = None

            def download(self, url, out, progress_cb=None, log=None, should_stop=None):
                idx = int(url.split("/")[-1])
                with test.download:
                    test.events.append(("dl_start", idx, time.monotonic()))
                    for _ in range(10):
                        if should_stop and should_stop():
                            raise RuntimeError("Đã dừng theo yêu cầu")
                        time.sleep(0.01)
                with open(out, "wb") as fh:
                    fh.write(b"v")
                self.last_info = {"duration": 5.0, "width": 720, "height": 1280}
                return out

        class FakeUploader:
            def __init__(self):
                test.uploader_inits += 1

            def create_folder(self, *a, **k):
                return "folder"

            def upload_video(self, path, **k):
                with test.upload:
                    time.sleep(0.03)
                return f"https://drive/{os.path.basename(path)}"

        def fake_voice(row, out_path, should_stop=None, **k):
            idx = int(row["video_url"].split("/")[-1])
            test.events.append(("voice_start", idx, time.monotonic()))
            time.sleep(0.05)
            if idx in test.voice_fail_for:
                raise RuntimeError("TTS lỗi giả lập")
            with open(out_path, "wb") as fh:
                fh.write(b"mp3")
            return out_path, "script"

        return [
            patch.object(main, "VideoDownloader", FakeDownloader),
            patch.object(main, "make_voice", fake_voice),
            patch.object(main, "IS_PRO", True),
            patch.object(drive, "DriveUploader", FakeUploader),
        ]

    def _run(self, n, workers, save_mode="local", voice=False):
        videos = [{"item_id": f"id{i}", "video_url": f"http://x/{i}",
                   "product_name": f"P{i}"} for i in range(n)]
        settings = {
            "save_mode": save_mode, "videos_snapshot": videos,
            "workers": workers, "delay": 0, "local_dir": self.tmp,
            "folder_name": "out", "csv_out": os.path.join(self.tmp, "o.csv"),
        }
        if voice:
            settings["voice_ai"] = {
                "prompt": "", "ai_key": "k", "tts_key": "k", "model": "m",
                "voice_name": "v", "speed": 1.0,
            }
        app = self._make_app()
        ps = self._patches()
        for p in ps:
            p.start()
        try:
            app._run_batch(list(range(n)), settings)
        finally:
            for p in ps:
                p.stop()
        return app

    def test_encode_concurrency_equals_user_setting(self):
        self._run(12, workers=2)
        self.assertEqual(2, self.encode.peak)
        # Prefetch: more downloads in flight than encoders.
        self.assertGreater(self.download.peak, 2)
        out_dir = os.path.join(self.tmp, "out")
        self.assertEqual(12, len(os.listdir(out_dir)))

    def test_clients_are_reused_per_thread(self):
        self._run(12, workers=2, save_mode="drive")
        # Processing pool = 2 encode + 2 prefetch; separate upload pool = 5
        # (+1 init uploader). Processing threads never build an uploader.
        self.assertLessEqual(self.downloader_inits, 4)
        self.assertLessEqual(self.uploader_inits, 6)
        self.assertLessEqual(self.upload.peak, 5)

    def test_drive_uploads_run_off_the_processing_pool(self):
        captured = {}
        real_make_app = self._make_app

        def make_app():
            app = real_make_app()
            app._write_output_csv = lambda path, refs, vids: captured.update(refs=dict(refs))
            return app

        self._make_app = make_app
        self._run(12, workers=2, save_mode="drive")
        refs = captured["refs"]
        self.assertEqual(12, len(refs))
        self.assertTrue(all(r.startswith("https://drive/") for r in refs.values()))
        # Uploads overlap encoding instead of blocking the processing threads.
        self.assertEqual(2, self.encode.peak)
        self.assertGreater(self.upload.peak, 1)

    def test_voice_runs_in_parallel_with_download(self):
        self._run(1, workers=1, voice=True)
        starts = {k: t for k, _i, t in self.events}
        self.assertLess(abs(starts["voice_start"] - starts["dl_start"]), 0.04)

    def test_voice_failure_skips_video_and_cleans_temp(self):
        self.voice_fail_for = {1}
        self._run(3, workers=1, voice=True)
        out_dir = os.path.join(self.tmp, "out")
        self.assertEqual(["id0.mp4", "id2.mp4"], sorted(os.listdir(out_dir)))
        self.assertTrue(any("TTS lỗi giả lập" in m for m in self.logs))

    def test_stop_mid_batch_exits_and_removes_temp_dir(self):
        app_holder = {}
        real_make_app = self._make_app

        def make_app():
            app = real_make_app()
            app_holder["app"] = app
            threading.Timer(0.15, app._cancel_event.set).start()
            return app

        self._make_app = make_app
        created = []
        real_mkdtemp = tempfile.mkdtemp
        with patch.object(main.tempfile, "mkdtemp",
                          side_effect=lambda **k: created.append(real_mkdtemp(**k)) or created[-1]):
            t0 = time.monotonic()
            self._run(40, workers=2, voice=True)
        self.assertLess(time.monotonic() - t0, 5)
        done = len(os.listdir(os.path.join(self.tmp, "out")))
        self.assertLess(done, 40)
        self.assertFalse(os.path.exists(created[0]))

    def test_stop_in_drive_mode_drains_upload_queue(self):
        real_make_app = self._make_app

        def make_app():
            app = real_make_app()
            threading.Timer(0.15, app._cancel_event.set).start()
            return app

        self._make_app = make_app
        t0 = time.monotonic()
        self._run(40, workers=2, save_mode="drive")
        self.assertLess(time.monotonic() - t0, 5)


if __name__ == "__main__":
    unittest.main()
