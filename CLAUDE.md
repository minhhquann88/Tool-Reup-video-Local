# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"Render Video Reup Pro" is a desktop GUI app (CustomTkinter) that batch-reprocesses videos from a CSV/XLSX list. For each row it downloads the video, optionally generates an AI voiceover, re-encodes it with FFmpeg (logo overlay, audio replacement), then saves it to Google Drive or a local folder. It finishes by writing an output CSV in which the `video_url` column is replaced with `Link Video` (Drive link or local path, `"Lỗi"` on error, empty if not run). User-facing strings, logs and most comments are in Vietnamese; keep new UI text in Vietnamese.

## Commands

```bash
# Run from source (Windows: installs deps, fetches FFmpeg into bin/ if missing, launches)
run.bat
# Linux equivalent (creates .venv, needs Python >= 3.10 + python3-tk)
./run_linux.sh
# Direct launch
python main.py

# Tests (pytest; tests/conftest.py adds repo root to sys.path and redirects crash/error logs to tmp)
python -m pytest tests
python -m pytest tests/test_encode_args.py
python -m pytest tests/test_batch_pipeline.py -k <name>

# Build Windows exe (PyInstaller onedir, requires client_secret.json + bin/ffmpeg.exe)
build.bat
# Build Linux AppImage (PACKAGE=tar for .tar.gz); Docker variant uses ubuntu:22.04
bash build_linux.sh
bash build_appimage_docker.sh
```

CI (`.github/workflows/build-linux.yml`) builds the AppImage on ubuntu-22.04 on every push to `main`, runs `tests/linux_x11_render_stress.py` under xvfb against the bundled libXft, and attaches a Release on `v*` tags.

`bin/` holds the FFmpeg/FFprobe binaries via **Git LFS**. If they are tiny pointer files, run `git lfs pull`. `core._local_or_system()` falls back to the system `ffmpeg` on PATH.

## Architecture

- **`main.py`** (~2.6k lines) holds the entire GUI (`App`, `VideoRow`, `LicenseDialog`), the batch pipeline (`App._run_batch`), CSV/XLSX input and output, and the `main()` entry point (single-instance check, then the license gate loop, then `App`).
- **`core.py`**: `VideoProcessor` builds and runs the FFmpeg commands (logo motion, position and opacity tables live here) and `VideoDownloader` handles HTTP, Google Drive and TikTok link downloads. Both are cancellable through `cancel_active()`.
- **`tts.py`**: generates a voiceover script from a CSV row with Gemini or OpenAI (`replace_prompt_variables` fills `{column}` placeholders), then synthesizes it with one of several TTS providers (Google TTS, autovoice.vn, videoai). The result is validated with `_validate_audio_file`.
- **`drive.py`**: `DriveUploader` handles OAuth desktop flow with `client_secret.json` and `token.json` stored next to the exe, folder creation, uploads and network retries. Drive clients are **not thread-safe**, so each worker thread keeps its own instance in thread-local storage.
- **`license.py`**: checks the key against `key.byscom.vn`. The API always returns HTTP 200, so read `status` in the JSON body. `APP_ID == "tool_reup_video_pro"` defines `IS_PRO`, which changes behavior across the app (see below). The app rechecks the license periodically, and the interval can be overridden with the `LICENSE_RECHECK_SECONDS`, `LICENSE_RETRY_SECONDS` and `LICENSE_MAX_RETRIES` environment variables.
- **`error_text.py`**: `explain(exc, stage)` and `short_reason()` turn exceptions into short Vietnamese messages with an error code (for example `NET-DRIVE`). Full tracebacks go to `app_error.log`.
- **`single_instance.py`**: uses a kernel mutex on Windows and an abstract socket on Linux. It is enforced only in the non-Pro build.

### Batch pipeline (`App._run_batch`)

The pipeline runs in stages on a thread pool:
- `workers` (set by the user) caps concurrent FFmpeg encodes through `_ffmpeg_sem`.
- The pool has extra threads (`_PREFETCH`) so downloads and voice generation run ahead of encoding.
- In Pro + Drive mode, uploads run on a separate `upload_pool`. `_upload_slots` provides backpressure by capping how many encoded files can wait on disk.
- The non-Pro build runs with `pool_size = workers` and one upload at a time.
- `delay` staggers video start times through `_gate_start`.
- Results are keyed by `id(video_dict)` in `out_refs`, so duplicate `item_id`s still work.

Stop requests go through `self._cancel_event`, and every long operation accepts a `should_stop` callable. A video that has already uploaded is always recorded, even after Stop.

### Threading rule

Only the Tk thread may touch widgets. Background code must call `self._ui(lambda: ...)`, which queues work drained every 25 ms by `_drain_ui_queue`. Logging goes through `_log_msg`/`_log_add` with a category from `LOG_CATS`. The UI keeps only the last `MAX_UI_LOGS` lines.

### Paths and frozen builds

Each module has its own `_app_dir`/`_bundle_dir`/`_exe_dir` helper:
- Bundled assets (`bin/`, icons, fontconfig) come from `sys._MEIPASS` when frozen.
- User files (`token.json`, `license.json`, `app_error.log`) live next to the exe or AppImage.

Keep this split when adding resources. Also add any new resource to the `--add-data` lists in `build.bat` and `build_linux.sh`.

### Linux/X11 specifics

Tk on X11 can crash with `BadLength (RenderAddGlyphs)`. The workarounds are:
- environment variables set at the top of `main.py` before Tk is imported,
- `linux-fontconfig.conf` and `packaging/linux-fontconfig-bundled.conf`,
- the CTk `DrawEngine` polygon mode,
- `_apply_linux_x11_fix`,
- a libXft 2.3.8 built from source and bundled (`packaging/build-libxft.sh`).

Don't reorder the imports at the top of `main.py`. `tests/test_linux_x11_packaging.py` and `tests/check_bundled_xft.py` guard this setup. Details are in `BUILD_UBUNTU_AGENT_GUIDE.md` and `docs/BUILD_LINUX.md`; the latter is partly outdated compared with the scripts.

Code must stay Python 3.8-compatible at the syntax level (`from __future__ import annotations` is used for `X | None`). The Linux run script requires 3.10 or later.

Design specs for past features (license key, local save mode, logo motion, log optimization) are in `docs/`.
