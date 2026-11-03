"""Build and verify a standalone Windows executable in an isolated environment."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


sys.path.insert(0, str(ROOT))
import edition  # noqa: E402  (không phụ thuộc thư viện ngoài)


def release_constants(name: str | None = None):
    """(hằng số license.py + APP_ID, tên file EXE) của bản `name` (mặc định: bản hiện hành)."""
    values = {}
    for node in ast.parse((ROOT / "license.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value
    profile = edition.profile_for(name) if name else edition.PROFILE
    values["APP_ID"] = profile["app_id"]
    return values, f"{profile['base_name']}_v{values['APP_VERSION']}"


def preflight():
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Build Windows cần Python 64-bit chạy trên Windows")
    if sys.version_info < (3, 10):
        raise RuntimeError("Cần Python 3.10 trở lên; khuyên dùng Python 3.12 64-bit")
    import setup_ffmpeg
    if any(not setup_ffmpeg._is_real_binary(ROOT / "bin" / name)
           for name in ("ffmpeg.exe", "ffprobe.exe")):
        if not setup_ffmpeg.setup():
            raise RuntimeError("Thiếu FFmpeg/FFprobe Windows thật; không thể dùng pointer Git LFS")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        with (ROOT / "bin" / name).open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise RuntimeError(name + " không phải binary Windows")
        subprocess.run([str(ROOT / "bin" / name), "-version"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    credentials = json.loads((ROOT / "client_secret.json").read_text(encoding="utf-8"))
    if not all(credentials.get("installed", {}).get(key)
               for key in ("client_id", "client_secret", "auth_uri", "token_uri")):
        raise RuntimeError("client_secret.json cần cấu hình Google OAuth Desktop hợp lệ")
    for name in ("video.ico", "video.png"):
        if not (ROOT / name).is_file():
            raise RuntimeError("Thiếu tài nguyên " + name)
    # Failing here is preferable to distributing an EXE without Tcl/Tk data.
    import tkinter
    root = tkinter.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    from PyInstaller.utils.hooks.tcl_tk import tcltk_info
    if not tcltk_info.available or not tcltk_info.data_files:
        raise RuntimeError("PyInstaller không thu thập được Tcl/Tk. Hãy sửa cài đặt Python rồi build lại.")
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)


def clean_test_environment(directory: Path):
    env = dict(os.environ)
    for name in list(env):
        if (name.startswith(("PYTHON", "_PYI_", "PYINSTALLER_"))
                or name in ("TCL_LIBRARY", "TK_LIBRARY", "VIRTUAL_ENV", "REUP_UPDATE_READY")):
            env.pop(name, None)
    windows = Path(env.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
    env["LOCALAPPDATA"] = str(directory / "profile")
    env["APPDATA"] = str(directory / "profile-roaming")
    env["TMP"] = env["TEMP"] = str(directory / "temp")
    (directory / "temp").mkdir(parents=True, exist_ok=True)
    return env


def verify_executable(executable: Path, name: str):
    # Only the EXE is copied: no source, Python, FFmpeg or _internal next to it.
    # A Unicode path also checks common Windows path/encoding mistakes.
    folder = ROOT / "build" / "windows-onefile-check" / "Máy khác - kiểm thử"
    folder.mkdir(parents=True, exist_ok=True)
    app_folder = folder / "read-only-app"
    app_folder.mkdir(exist_ok=True)
    copied = app_folder / "Render Video Reup.exe"
    shutil.copy2(str(executable), str(copied))
    report = folder / "self-test.json"
    report.unlink(missing_ok=True)
    acl_tool = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    if ROOT / "build" not in app_folder.resolve().parents:
        raise RuntimeError("Thư mục kiểm tra nằm ngoài build/")
    try:
        # Only this generated directory loses file/subdirectory creation rights.
        # Reports and extraction temp directories stay in its writable parent.
        subprocess.run([str(acl_tool), str(app_folder), "/deny", "*S-1-1-0:(WD,AD)"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run([str(copied), "--self-test", str(report)], cwd=str(app_folder),
                       env=clean_test_environment(folder), check=True, timeout=240)
    finally:
        subprocess.run([str(acl_tool), str(app_folder), "/remove:d", "*S-1-1-0"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result = json.loads(report.read_text(encoding="utf-8"))
    if not result.get("ok") or result.get("distribution") != "onefile":
        raise RuntimeError("Bản EXE không qua kiểm tra: " + str(report))
    expected = edition.profile_for(name)["app_id"]
    if (result.get("edition"), result.get("edition_source"), result.get("app_id")) != (name, "stamp", expected):
        raise RuntimeError(f"EXE không đóng cứng đúng bản '{name}' ({expected}): " + str(report))
    return report


def audit_archive(executable: Path):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    names = {name.replace("\\", "/") for name in archive.toc}
    required = {"bin/ffmpeg.exe", "bin/ffprobe.exe", "client_secret.json", "video.ico", "video.png",
                "vcruntime140.dll", "vcruntime140_1.dll", "tcl86t.dll", "tk86t.dll", "_tkinter.pyd",
                "_tcl_data/init.tcl", "_tk_data/tk.tcl"}
    missing = required - names
    if missing:
        raise RuntimeError("EXE thiếu tài nguyên/thư viện: " + ", ".join(sorted(missing)))
    if any(name.rsplit("/", 1)[-1] in ("token.json", "license.json") for name in names):
        raise RuntimeError("EXE chứa dữ liệu đăng nhập/bản quyền người dùng; không phân phối")
    if names.intersection({"bin/ffmpeg", "bin/ffprobe"}):
        raise RuntimeError("Không đóng kèm binary Linux trong bản Windows")
    return {"entries": len(names), "required_files": sorted(required)}


def clean_previous_outputs(dist_dir: Path, name: str):
    # One release = one EXE per edition in dist; drop this edition's older
    # EXEs/old setup.exe so nothing is mixed up, but keep the other edition's.
    if not dist_dir.is_dir():
        return
    base = edition.profile_for(name)["base_name"]
    for path in dist_dir.glob(base + "*.exe"):
        if path.name[len(base):][:1] not in ("_", "-", "."):
            continue                       # "RenderVideoReup" không xóa "RenderVideoReupPro…"
        try:
            path.unlink()
        except OSError as exc:
            raise RuntimeError("Không xóa được " + str(path) + ". Hãy đóng app đang chạy từ file này rồi build lại.") from exc


def build(dist_dir: Path, edition_name: str):
    preflight()
    clean_previous_outputs(dist_dir, edition_name)
    _, name = release_constants(edition_name)
    env = dict(os.environ)
    env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-cache")
    env[edition.ENV_VAR] = edition_name     # spec đọc để đặt tên + đóng cứng bản
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(dist_dir),
                    "--workpath", str(ROOT / "build" / "windows-onefile"),
                    str(ROOT / "packaging" / "windows-onefile.spec")],
                   cwd=str(ROOT), env=env, check=True)
    executable = dist_dir / (name + ".exe")
    audit = audit_archive(executable)
    report = verify_executable(executable, edition_name)
    digest = hashlib.sha256()
    with executable.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    build_report = ROOT / "build" / f"windows-onefile-report-{edition_name}.json"
    build_report.write_text(json.dumps({
        "executable": str(executable), "bytes": executable.stat().st_size,
        "edition": edition_name, "app_id": edition.profile_for(edition_name)["app_id"],
        "sha256": digest.hexdigest(), "python": sys.version,
        "platform": platform.platform(), "self_test": str(report),
        "archive_audit": audit,
        "dependencies": {dist.metadata["Name"]: dist.version
                         for dist in importlib.metadata.distributions()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nBUILD OK: " + str(executable))
    print("SHA-256: " + digest.hexdigest())
    print("Chi phan phoi file .exe nay. Bao cao: " + str(build_report))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--edition", choices=sorted(edition.PROFILES),
                        default=os.environ.get(edition.ENV_VAR, "").strip().lower() or edition.DEFAULT_EDITION,
                        help="Bản cần build: pro (mặc định) hoặc khach (bản thường)")
    args = parser.parse_args()
    if args.worker:
        build(args.dist_dir.resolve(), args.edition)
        return
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Hãy build bằng Python 64-bit trên Windows")
    env_dir = ROOT / ".venv-build-windows"
    python = env_dir / "Scripts" / "python.exe"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(env_dir)], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "-r", str(ROOT / "requirements-windows-build.txt")], check=True)
    subprocess.run([str(python), str(Path(__file__)), "--worker", "--dist-dir", str(args.dist_dir.resolve()),
                    "--edition", args.edition],
                   cwd=str(ROOT), check=True)


if __name__ == "__main__":
    main()
