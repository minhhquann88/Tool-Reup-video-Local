"""Offline tests executed by the actual frozen EXE before a build is accepted."""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


def run(report_path: Path) -> bool:
    result = {"ok": False, "checks": [], "distribution": "source"}
    try:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        if getattr(sys, "frozen", False):
            result["distribution"] = "onedir" if root == Path(sys.executable).parent / "_internal" else "onefile"
        result["bundle"] = str(root)
        import certifi
        import requests
        import openpyxl
        from PIL import Image, ImageTk
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google_auth_oauthlib.flow import InstalledAppFlow
        import customtkinter as ctk
        from tkinter import ttk
        import core
        import license as license_mod
        import updater
        ssl.create_default_context(cafile=certifi.where())
        requests.Session().prepare_request(requests.Request("GET", "https://example.com"))
        result["checks"].append("TLS certificates / requests")
        with (root / "client_secret.json").open(encoding="utf-8") as stream:
            config = json.load(stream)
        InstalledAppFlow.from_client_config(config, ["https://www.googleapis.com/auth/drive"])
        service = build("drive", "v3", credentials=Credentials(token="offline-self-test"),
                        static_discovery=True, cache_discovery=False)
        service.files().list(pageSize=1)  # Build only; no network request or real key.
        service.close()
        result["checks"].append("Google Drive discovery / OAuth")
        window = ctk.CTk()
        try:
            window.withdraw()
            ctk.CTkLabel(window, text="Kiểm tra tiếng Việt").pack()
            ctk.CTkButton(window, text="Kiểm tra").pack()
            ctk.CTkSwitch(window, text="Bật / tắt").pack()
            ttk.Progressbar(window).pack()
            with Image.open(root / "video.png") as image:
                photo = ImageTk.PhotoImage(image.resize((32, 32)), master=window)
                assert photo.width() == 32
            window.iconbitmap(str(root / "video.ico"))
            window.update()
        finally:
            window.destroy()
        result["checks"].append("Tcl/Tk / CustomTkinter / Pillow / updater widgets")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="exe-check-", dir=str(report_path.parent)) as work:
            folder = Path(work)
            _check_auth_storage(folder)
            result["checks"].append("Fixed per-user auth storage / legacy migration / logout tombstones")
            workbook = openpyxl.Workbook()
            workbook.active.append(["product_name", "Sản phẩm thử nghiệm"])
            workbook.save(folder / "test.xlsx")
            loaded = openpyxl.load_workbook(folder / "test.xlsx")
            assert loaded.active.cell(1, 2).value == "Sản phẩm thử nghiệm"
            loaded.close()
            result["checks"].append("Excel read / write")
            for binary in (core.FFMPEG, core.FFPROBE):
                if root.resolve() not in Path(binary).resolve().parents:
                    raise RuntimeError("FFmpeg/FFprobe đang phụ thuộc PATH của máy build")
            source, output = folder / "video tiếng Việt.mp4", folder / "kết quả.mp4"
            subprocess.run([
                core.FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "testsrc2=size=320x180:rate=15", "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            core.VideoProcessor().process_video(str(source), str(output), {
                "logo_path": str(root / "video.png"), "logo_size": 15,
                "trim_start": 0.1, "audio_path": None,
            })
            info = core.probe_video(str(output))
            if not info or info["width"] != 320:
                raise RuntimeError("Không render hoặc đọc được video kiểm tra")
            result["checks"].append("Bundled FFmpeg / FFprobe / H264 / AAC / logo / Unicode paths")
        import edition
        result["version"] = license_mod.APP_VERSION
        result["edition"] = edition.EDITION
        result["edition_source"] = edition.EDITION_SOURCE
        result["app_id"] = license_mod.APP_ID
        result["updater_imported"] = updater.READY_ENV == "REUP_UPDATE_READY"
        result["ok"] = True
    except Exception:
        result["error"] = traceback.format_exc()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result["ok"]


def _check_auth_storage(folder: Path):
    import app_paths
    import drive
    import license as license_mod
    names = ("LOCALAPPDATA", "XDG_CONFIG_HOME")
    old_env = {name: os.environ.get(name) for name in names}
    old_legacy = app_paths.legacy_dirs
    legacy = folder / "old-app"
    legacy.mkdir()
    (legacy / "license.json").write_text('{"key":"OFFLINE-SELF-TEST"}', encoding="utf-8")
    token = {"refresh_token": "test", "client_id": "test", "client_secret": "test"}
    (legacy / "token.json").write_text(json.dumps(token), encoding="utf-8")
    try:
        os.environ["LOCALAPPDATA"] = str(folder / "user-data")
        os.environ["XDG_CONFIG_HOME"] = str(folder / "user-data")
        app_paths.legacy_dirs = lambda: [legacy]
        if sys.platform == "darwin":
            # This diagnostic is used by Windows builds; keep other platforms
            # from touching their real user profile if invoked manually.
            return
        assert license_mod.load_saved_key() == "OFFLINE-SELF-TEST"
        path = drive.get_token_path()
        data, revision = app_paths.read_auth(path)
        assert data == token
        assert folder / "user-data" in path.parents
        drive.clear_credentials()
        assert not app_paths.write_auth(path, token, revision)
        license_mod.clear_key()
        app_paths.migrate_legacy_auth()
        assert license_mod.load_saved_key() is None
        assert app_paths.read_auth(path)[0] is None
        assert (legacy / "license.json").exists()
    finally:
        app_paths.legacy_dirs = old_legacy
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
