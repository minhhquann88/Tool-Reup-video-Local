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

# Build one standalone Windows EXE, including isolated dependencies + frozen self-test
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
- **`drive.py`**: `DriveUploader` handles OAuth desktop flow with bundled `client_secret.json` and per-user `token.json`, folder creation, uploads and network retries. Drive clients are **not thread-safe**, so each worker thread keeps its own instance in thread-local storage.
- **`app_paths.py`**: fixed per-user paths shared by source and frozen builds, legacy auth migration, cross-process file locking, atomic writes, revision checks and logout tombstones. Tests isolate profile paths and legacy lookup; never migrate real credentials in a test.
- **`edition.py`**: the single place that defines the two editions, Pro (`tool_reup_video_pro`) and standard/khách (`tool_reup_video`): `app_id`, file/data-dir base name, title and the thread limits of every pipeline stage (`main.PROFILE`). Both editions run the same pipeline code and differ only in these numbers. Both editions are built from `main`; never compare `app_id` strings elsewhere. A build stamps the edition into a gitignored `_edition_stamp.py`, and frozen builds read only that. Source runs default to Pro, and `REUP_EDITION=khach` selects the standard edition (run the test suite under both).
- **`license.py`**: checks the key against `key.byscom.vn`. The API always returns HTTP 200, so read `status` in the JSON body. `APP_ID` comes from `edition.py`. The app rechecks the license periodically, and the interval can be overridden with the `LICENSE_RECHECK_SECONDS`, `LICENSE_RETRY_SECONDS` and `LICENSE_MAX_RETRIES` environment variables.
- **`updater.py`**: independent updater process with its own Tk progress window. It downloads and stages releases, waits for the parent app to save/exit, transactionally replaces program files, then waits for the new GUI's version handshake before closing. Windows onefile helpers copy just the EXE and reset PyInstaller's runtime environment; onedir helpers copy their runtime too. User data stays untouched. See `docs/KIEM_TRA_PHIEN_BAN.md` for release formats and local HTTP simulation tests.
- **`build_windows.py`**, **`packaging/windows-onefile.spec`**: isolated Windows build with pinned direct dependencies, resource validation and an actual frozen EXE self-test (`packaging_smoke.py`). See `docs/BUILD_WINDOWS.md`.
- **`error_text.py`**: `explain(exc, stage)` and `short_reason()` turn exceptions into short Vietnamese messages with an error code (for example `NET-DRIVE`). Full tracebacks go to `app_error.log`.
- **`single_instance.py`**: uses a kernel mutex on Windows and an abstract socket on Linux. It is enforced only when the edition's `single_instance` is set (standard edition), so extra windows cannot bypass its thread limits.

### Batch pipeline (`App._run_batch`)

The pipeline runs in stages on a thread pool. Every number below comes from `edition.PROFILE`; Pro values first, standard edition in brackets:
- `workers` caps concurrent FFmpeg encodes through `_ffmpeg_sem` (user-set up to 8, default 2 [fixed 1]).
- `pool_size = workers + _PREFETCH`; the `_PREFETCH` extra threads let downloads and voice generation run ahead of encoding (2 [0]).
- With `split_upload_pool` + Drive, uploads run on a separate `upload_pool` (5 [no split: upload inline, 1 at a time]). `_upload_slots` provides backpressure by capping how many encoded files can wait on disk.
- `delay` staggers video start times through `_gate_start` (user-set, default 0 [at least 30 s, enforced in `_run_batch` too]).
- Results are keyed by `id(video_dict)` in `out_refs`, so duplicate `item_id`s still work.

Stop requests go through `self._cancel_event`, and every long operation accepts a `should_stop` callable. A video that has already uploaded is always recorded, even after Stop.

### Threading rule

Only the Tk thread may touch widgets. Background code must call `self._ui(lambda: ...)`, which queues work drained every 25 ms by `_drain_ui_queue`. Logging goes through `_log_msg`/`_log_add` with a category from `LOG_CATS`. The UI keeps the last `MAX_UI_LOGS` (1,000) non-error lines and all errors for the current app session, including across batches. Errors also append to `app_error.log` on disk.

### Paths and frozen builds

Each module has its own `_app_dir`/`_bundle_dir`/`_exe_dir` helper:
- Bundled assets (`bin/`, icons, fontconfig) come from `sys._MEIPASS` when frozen.
- User auth files and logs live in `%LOCALAPPDATA%/RenderVideoReupPro` on Windows or `${XDG_CONFIG_HOME:-~/.config}/RenderVideoReupPro` on Linux (standard edition uses `RenderVideoReup`). Migration and concurrency details are in `docs/KE_HOACH_LUU_DU_LIEU_UNG_DUNG.md`.

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
