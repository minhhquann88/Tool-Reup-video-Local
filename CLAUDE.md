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
# Direct launch (Pro); standard edition from source
python main.py
REUP_EDITION=khach python main.py

# Tests (pytest; tests/conftest.py adds repo root to sys.path and redirects crash/error logs to tmp).
# Run the whole suite under both editions.
python -m pytest tests
REUP_EDITION=khach python -m pytest tests
python -m pytest tests/test_batch_pipeline.py -k <name>

# Build one standalone Windows EXE per edition, including isolated dependencies + frozen self-test
build.bat                 # Pro → dist/RenderVideoReupPro_v*.exe
build.bat khach           # standard → dist/RenderVideoReup_v*.exe
build.bat all
# Build Linux AppImage (PACKAGE=tar for .tar.gz); Docker variant uses ubuntu:22.04
EDITION=khach bash build_linux.sh
EDITION=khach bash build_appimage_docker.sh
# Source package / self-extracting installer (also take EDITION=khach)
bash package_source.sh
bash make_installer.sh
```

Every build stamps the edition with `edition.write_stamp()` outside the source tree (`build/edition-stamp/<edition>/_edition_stamp.py`, added via `pathex`/`--paths`) and verifies it: the Windows self-test must report `edition_source == "stamp"`, and Linux runs `packaging/verify_edition.py`. Source packages and installers ship the stamp file itself.

CI (`.github/workflows/build-windows.yml`, `build-linux.yml`) builds both editions as a matrix on every push to `main`. The Linux job runs `tests/linux_x11_render_stress.py` under xvfb against the bundled libXft. On `v*` tags, both workflows attach all four files to one Release.

`bin/` holds the FFmpeg/FFprobe binaries via **Git LFS**. If they are tiny pointer files, run `git lfs pull`. `core._local_or_system()` falls back to the system `ffmpeg` on PATH.

## Architecture

- **`main.py`** (~2.6k lines) holds the entire GUI (`App`, `VideoRow`, `LicenseDialog`), the batch pipeline (`App._run_batch`), CSV/XLSX input and output, and the `main()` entry point (single-instance check, then the license gate loop, then `App`).
- **`core.py`**: `VideoProcessor` builds and runs the FFmpeg commands (logo motion, position and opacity tables live here) and `VideoDownloader` handles HTTP, Google Drive and TikTok link downloads. Both are cancellable through `cancel_active()`.
- **`tts.py`**: generates a voiceover script from a CSV row with Gemini or OpenAI (`replace_prompt_variables` fills `{column}` placeholders), then synthesizes it with one of several TTS providers (Google TTS, autovoice.vn, videoai). The result is validated with `_validate_audio_file`.
- **`drive.py`**: `DriveUploader` handles OAuth desktop flow with bundled `client_secret.json` and per-user `token.json`, folder creation, uploads and network retries. Multiple accounts: `token.json` is always the **active** account (batch code only ever reads it); `drive_accounts.json` stores every signed-in account (`add_account`, `switch_account`, `remove_account`, `list_accounts`). Switching copies tokens between the two and is blocked while a batch runs. The UI lists the accounts in a box (click an email to switch, "Thoát" to log that one out). The tool never opens a browser: `add_account(on_auth_url)` hands the OAuth URL to the UI, the user copies it into a browser on the same machine, and "Hủy" cancels the wait. Batches never start a login; `_get_credentials` raises when no account is signed in. Drive clients are **not thread-safe**, so each worker thread keeps its own instance in thread-local storage.
- **`app_paths.py`**: fixed per-user paths shared by source and frozen builds, legacy auth migration, cross-process file locking, atomic writes, revision checks and logout tombstones. Tests isolate profile paths and legacy lookup; never migrate real credentials in a test.
- **`edition.py`**: the single place that defines the two editions, Pro (`tool_reup_video_pro`) and standard/khách (`tool_reup_video`): `app_id`, file/data-dir base name, title and the thread limits of every pipeline stage (`main.PROFILE`). Both editions run the same pipeline code and differ only in these numbers. Both editions are built from `main`; never compare `app_id` strings elsewhere. A build stamps the edition into a gitignored `_edition_stamp.py`, and frozen builds read only that (`EDITION_SOURCE` says where the edition came from). Source runs default to Pro, and `REUP_EDITION=khach` selects the standard edition (run the test suite under both).
- **`license.py`**: checks the key against `key.byscom.vn`. The API always returns HTTP 200, so read `status` in the JSON body. `APP_ID` comes from `edition.py`. The app rechecks the license periodically, and the interval can be overridden with the `LICENSE_RECHECK_SECONDS`, `LICENSE_RETRY_SECONDS` and `LICENSE_MAX_RETRIES` environment variables.
- **`updater.py`**: independent updater process with its own Tk progress window. It downloads and stages releases, waits for the parent app to save/exit, transactionally replaces program files, then waits for the new GUI's handshake (version and `app_id`) before closing; a package of the other edition is refused by file name before download, or rolled back at the handshake. Windows onefile helpers copy just the EXE and reset PyInstaller's runtime environment; onedir helpers copy their runtime too. User data stays untouched. See `docs/KIEM_TRA_PHIEN_BAN.md` for release formats and local HTTP simulation tests.
- **`build_windows.py`**, **`packaging/windows-onefile.spec`**: isolated Windows build with pinned direct dependencies, resource validation and an actual frozen EXE self-test (`packaging_smoke.py`). See `docs/BUILD_WINDOWS.md`.
- **`error_text.py`**: `explain(exc, stage)` and `short_reason()` turn exceptions into short Vietnamese messages with an error code (for example `NET-DRIVE`). Full tracebacks go to `app_error.log`.
- **`hwinfo.py`**: reads the user's machine without extra dependencies (ctypes on Windows, `/sys` + `/proc` on Linux). `usable_threads()` counts the CPU threads this process may run on (Windows 10 limits a process to one processor group of ≤64 threads) and drops CPUs of NUMA nodes with no local RAM; any failure falls back to `os.cpu_count()`.
- **`single_instance.py`**: uses a kernel mutex on Windows and an abstract socket on Linux. It is enforced only when the edition's `single_instance` is set (standard edition), so extra windows cannot bypass its thread limits.

### Batch pipeline (`App._run_batch`)

The pipeline runs in stages on a thread pool. Every number below comes from `edition.PROFILE`; Pro values first, standard edition in brackets:
- `workers` caps concurrent FFmpeg encodes (user-set up to 8; default `edition.default_workers_for` on `hwinfo.usable_threads()`: usable threads ÷ 4, clamped 2–6 [fixed 1]). `hwinfo.plan_encode_slots` turns that into encode slots held in a queue (`_free_slots`): usable threads are split evenly so each FFmpeg gets `-threads N` for decode, filters and x264 instead of `-threads 0` (several FFmpeg each grabbing every thread fight each other: measured +4% on 16 threads, +8% on 36). Machines with ≤ `hwinfo.SPLIT_MIN_THREADS` (8) usable threads keep `-threads 0`, which measured 1–2% faster there. Fewer slots are used when free RAM is low.
- `pool_size = workers + _PREFETCH`; the `_PREFETCH` extra threads let downloads and voice generation run ahead of encoding (`edition.prefetch_for`: number of encodes clamped 2–4 [0]).
- With `split_upload_pool` + Drive, uploads run on a separate `upload_pool` (5 [no split: upload inline, 1 at a time]). `_upload_slots` provides backpressure by capping how many encoded files can wait on disk.
- `delay` staggers video start times through `_gate_start` (`default_delay`, not shown in the UI of either edition and enforced as a minimum in `_run_batch`: 0 [20]).
- Results are keyed by `id(video_dict)` in `out_refs`, so duplicate `item_id`s still work.

Stop requests go through `self._cancel_event`, and every long operation accepts a `should_stop` callable. A video that has already uploaded is always recorded, even after Stop.

The Stop button is labelled "KẾT THÚC" so it is not confused with Pause. Pause ("TẠM DỪNG"/"TIẾP TỤC") sets `self._pause_event`. Workers block in `_wait_while_paused()` at stage boundaries (start gate, before encode, before save/upload, start of `upload_one`), and `VideoProcessor.suspend_active()` freezes running FFmpeg processes (NtSuspendProcess on Windows, SIGSTOP on Linux; suspended time does not count toward the 10-minute timeout). In-flight downloads, uploads and voice requests finish their current step. Time spent paused is excluded from the batch clock, and the `_gate_start` delay re-reserves its slot after a resume.

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
