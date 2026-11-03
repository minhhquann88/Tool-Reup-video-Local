"""Independent updater: its window outlives the app while files are replaced."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import queue
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import requests

SOURCE_FILES = (
    "main.py", "core.py", "drive.py", "license.py", "tts.py", "updater.py",
    "single_instance.py", "error_text.py", "setup_ffmpeg.py", "requirements.txt",
    "run_linux.sh", "run.bat", "linux-fontconfig.conf", "video.png", "video.ico",
    "packaging_smoke.py", "app_paths.py", "edition.py", "_edition_stamp.py",
)
REQUIRED_SOURCE = ("main.py", "license.py", "updater.py", "app_paths.py", "core.py", "drive.py",
                   "tts.py", "error_text.py", "single_instance.py", "edition.py")
PROTECTED = {"license.json", "token.json", "client_secret.json", "app_error.log", "crash.log", ".auth-state.json", ".auth.lock"}
MAX_UNPACKED = 8 * 1024 ** 3
READY_ENV = "REUP_UPDATE_READY"
FILE_RETRY_SECONDS = 15
PYINSTALLER_COOKIE = b"MEI\x0c\x0b\x0a\x0b\x0e"


def write_json(path: Path, data: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(str(temporary), str(path))


def read_status(job_dir: Path) -> dict:
    try:
        return json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_status(job: dict, state: str, message: str = ""):
    write_json(Path(job["job_dir"]) / "status.json",
               {"state": state, "message": message, "pid": os.getpid()})


def process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied is not proof of exit.
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if resolved == root.resolve() or root.resolve() not in resolved.parents:
        raise ValueError("Đường dẫn cập nhật nằm ngoài thư mục cho phép")
    return resolved


def _remove(root: Path, path: Path):
    _inside(root, path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(str(path))


def child_environment() -> dict:
    env = dict(os.environ)
    env.pop(READY_ENV, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if getattr(sys, "frozen", False):
        # Independent PyInstaller/AppImage process must not load the old runtime.
        for name in ("LD_LIBRARY_PATH", "LIBPATH"):
            original = env.pop(name + "_ORIG", None)
            if original is None:
                env.pop(name, None)
            else:
                env[name] = original
        for name in ("FONTCONFIG_FILE", "FONTCONFIG_PATH", "TCL_LIBRARY", "TK_LIBRARY",
                     "APPIMAGE", "APPDIR", "OWD"):
            env.pop(name, None)
    return env


def _release_lock(job: dict):
    lock = Path(job["base_dir"]) / ".reup-update.lock"
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        if data.get("job_dir") == job["job_dir"]:
            lock.unlink()
    except (OSError, ValueError):
        pass


def create_job(info: dict) -> dict:
    import license as license_mod
    url = str(info.get("download_url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ValueError("Liên kết tải bản cập nhật không hợp lệ")
    appimage = os.environ.get("APPIMAGE") if getattr(sys, "frozen", False) else None
    if appimage:
        entry = Path(appimage).resolve()
        mode = "appimage"
    elif getattr(sys, "frozen", False):
        entry = Path(sys.executable).resolve()
        bundle = Path(getattr(sys, "_MEIPASS", entry.parent)).resolve()
        mode = ("onefile" if sys.platform == "win32"
                and bundle != (entry.parent / "_internal").resolve() else "frozen")
        if mode == "frozen" and not (entry.parent / "_internal").is_dir():
            raise ValueError("Cập nhật tự động cần bản đóng gói onedir hoặc AppImage")
    else:
        entry = Path(__file__).resolve().parent / "main.py"
        mode = "source"
    base = entry.parent
    # Check write permission before closing the app or downloading a large file.
    try:
        probe = tempfile.NamedTemporaryFile(prefix=".reup-write-", dir=str(base), delete=False)
    except OSError as exc:
        raise RuntimeError("Thư mục ứng dụng không cho ghi. Hãy chuyển file EXE vào thư mục riêng có quyền ghi rồi cập nhật lại.") from exc
    probe.close()
    Path(probe.name).unlink()
    job_dir = Path(tempfile.mkdtemp(prefix="reup-update-"))
    job = {"job_dir": str(job_dir), "base_dir": str(base), "entry": str(entry),
           "mode": mode, "url": url, "version": str(info.get("latest_version") or "").lstrip("v"),
           "previous_version": license_mod.APP_VERSION,
           "app_id": license_mod.APP_ID, "parent_pid": os.getpid(),
           "python": sys.executable, "platform": license_mod.get_platform_name(),
           "sha256": str(info.get("sha256") or "").lower()}
    if mode == "onefile":
        job["parent_bootloader_pid"] = os.getppid()
    lock = base / ".reup-update.lock"
    for attempt in range(2):
        try:
            fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "job_dir": str(job_dir)}, stream)
            break
        except FileExistsError:
            try:
                old = json.loads(lock.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = {"pid": os.getpid()}
            if attempt or process_running(int(old.get("pid", 0))):
                raise RuntimeError("Một cửa sổ khác đang cập nhật ứng dụng. Vui lòng chờ.")
            lock.unlink()
    try:
        write_json(job_dir / "job.json", job)
    except Exception:
        _release_lock(job)
        raise
    return job


def launch_helper(job: dict, progress) -> subprocess.Popen:
    """Copy runtime away from the install directory BEFORE the parent exits."""
    job_dir = Path(job["job_dir"])
    helper = job_dir / "helper"
    helper.mkdir()
    source = Path(job["entry"])
    progress("Chuẩn bị tiến trình cập nhật độc lập…", None)
    try:
        if job["mode"] == "source":
            worker = helper / "updater.py"
            shutil.copy2(str(Path(__file__)), str(worker))
            command = [job["python"], str(worker), "--worker", str(job_dir / "job.json")]
        else:
            worker = helper / source.name
            shutil.copy2(str(source), str(worker))
            if job["mode"] == "frozen":
                runtime = source.parent / "_internal"
                files = sum(1 for path in runtime.rglob("*") if path.is_file())
                copied = [0]

                def copy_file(src, dst):
                    result = shutil.copy2(src, dst)
                    copied[0] += 1
                    progress(f"Chuẩn bị bộ cập nhật: {copied[0]}/{files} file", copied[0] / max(1, files))
                    return result

                shutil.copytree(str(runtime), str(helper / "_internal"), copy_function=copy_file)
            command = [str(worker), "--reup-updater", str(job_dir / "job.json")]
        log = open(job_dir / "helper.log", "ab")
        try:
            process = subprocess.Popen(
                command, cwd=str(helper), env=child_environment(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            log.close()
        write_json(Path(job["base_dir"]) / ".reup-update.lock",
                   {"pid": process.pid, "job_dir": job["job_dir"]})
        return process
    except Exception:
        _release_lock(job)
        raise


def download(job: dict, progress) -> Path:
    target = Path(job["job_dir"]) / "release.part"
    digest = hashlib.sha256()
    received = 0
    last_report = 0.0
    progress("Đang kết nối máy chủ tải bản cập nhật…", None)
    with requests.get(job["url"], stream=True, timeout=(15, 45),
                      headers={"Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        if total > MAX_UNPACKED:
            raise ValueError("Gói cập nhật quá lớn")
        with target.open("wb") as stream:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > MAX_UNPACKED:
                    raise ValueError("Gói cập nhật vượt giới hạn dung lượng")
                stream.write(chunk)
                digest.update(chunk)
                if time.monotonic() - last_report > 0.1:
                    amount = f"{received / 1024 ** 2:.1f} MB"
                    if total:
                        amount += f" / {total / 1024 ** 2:.1f} MB ({received * 100 / total:.0f}%)"
                    progress("Đang tải bản cập nhật: " + amount, received / total if total else None)
                    last_report = time.monotonic()
    if not received or (total and received != total):
        raise ValueError("Gói cập nhật tải chưa đầy đủ. Vui lòng thử lại.")
    expected = job.get("sha256")
    if expected and digest.hexdigest() != expected:
        raise ValueError("Mã SHA-256 của gói cập nhật không khớp")
    progress("Đã tải xong. Đang kiểm tra gói cập nhật…", 1.0)
    return target


def _member_path(root: Path, name: str) -> Path:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
        raise ValueError("Gói cập nhật chứa đường dẫn không hợp lệ")
    return _inside(root, root.joinpath(*path.parts))


def extract_archive(archive: Path, root: Path, progress):
    root.mkdir()
    total_size = 0
    if zipfile.is_zipfile(str(archive)):
        with zipfile.ZipFile(str(archive)) as package:
            members = package.infolist()
            if len(members) > 30000:
                raise ValueError("Gói cập nhật chứa quá nhiều file")
            for index, member in enumerate(members):
                path = _member_path(root, member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("Gói cập nhật không được chứa liên kết tượng trưng")
                total_size += member.file_size
                if total_size > MAX_UNPACKED:
                    raise ValueError("Dung lượng giải nén vượt giới hạn")
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(member) as source, path.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    if member.external_attr >> 16:
                        path.chmod((member.external_attr >> 16) & 0o777)
                progress(f"Đang giải nén: {index + 1}/{len(members)} file", (index + 1) / len(members))
    elif tarfile.is_tarfile(str(archive)):
        with tarfile.open(str(archive)) as package:
            links = []
            for index, member in enumerate(package):
                if index >= 30000 or not (member.isfile() or member.isdir()
                                          or member.issym() or member.islnk()):
                    raise ValueError("Gói cập nhật chứa loại file không được hỗ trợ")
                path = _member_path(root, member.name)
                total_size += member.size
                if total_size > MAX_UNPACKED:
                    raise ValueError("Dung lượng giải nén vượt giới hạn")
                if member.issym() or member.islnk():
                    # Materialize only file links that stay inside this archive.
                    origin = path.parent if member.issym() else root
                    link = _inside(root, origin / member.linkname)
                    links.append((path, link))
                elif member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as source, path.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    path.chmod(member.mode & 0o777)
                progress(f"Đang giải nén: {index + 1} file", None)
            while links:
                pending = []
                for path, link in links:
                    if not link.is_file():
                        pending.append((path, link))
                        continue
                    total_size += link.stat().st_size
                    if total_size > MAX_UNPACKED:
                        raise ValueError("Dung lượng giải nén vượt giới hạn")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(link), str(path))
                if len(pending) == len(links):
                    raise ValueError("Gói cập nhật chứa liên kết không hợp lệ")
                links = pending
    else:
        raise ValueError("Cần gói ZIP, tar.gz hoặc AppImage phù hợp với bản đang chạy")


def _constants(path: Path) -> dict:
    values = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value
    return values


def _verify_source(root: Path, job: dict):
    import edition
    values = _constants(root / "license.py")
    stamp = root / "_edition_stamp.py"
    name = _constants(stamp).get("EDITION") if stamp.is_file() else edition.DEFAULT_EDITION
    app_id = edition.PROFILES.get(name, {}).get("app_id")
    if app_id != job["app_id"] or values.get("APP_VERSION") != job["version"]:
        raise ValueError("Gói cập nhật không đúng ứng dụng hoặc phiên bản được thông báo")


def package_entries(archive: Path, job: dict, progress) -> list:
    if job["mode"] == "onefile":
        with archive.open("rb") as stream:
            direct_exe = stream.read(2) == b"MZ"
        if direct_exe:
            verify_windows_onefile(archive)
            return [(archive, Path(job["entry"]).name)]
        root = Path(job["job_dir"]) / ("unpacked-" + str(time.time_ns()))
        extract_archive(archive, root, progress)
        candidates = [p for p in root.rglob("RenderVideoReup*.exe") if p.is_file()]
        if len(candidates) != 1 or (candidates[0].parent / "_internal").exists():
            raise ValueError("Cần file EXE standalone, hoặc ZIP chỉ chứa file EXE standalone")
        verify_windows_onefile(candidates[0])
        return [(candidates[0], Path(job["entry"]).name)]
    if job["mode"] == "appimage":
        with archive.open("rb") as stream:
            header = stream.read(12)
        if header[:4] != b"\x7fELF" or header[8:11] != b"AI\x02":
            raise ValueError("Bản AppImage cần liên kết tải trực tiếp file .AppImage")
        archive.chmod(0o755)
        return [(archive, Path(job["entry"]).name)]
    root = Path(job["job_dir"]) / ("unpacked-" + str(time.time_ns()))
    extract_archive(archive, root, progress)
    if job["mode"] == "source":
        candidates = [p.parent for p in root.rglob("main.py")
                      if all((p.parent / name).is_file() for name in REQUIRED_SOURCE)]
        if len(candidates) != 1:
            raise ValueError("Bản chạy Python cần gói mã nguồn, có đầy đủ các module ứng dụng")
        package = candidates[0]
        _verify_source(package, job)
        return [(package / name, name) for name in SOURCE_FILES if (package / name).is_file()]
    suffix = ".exe" if job["platform"] == "win" else ""
    candidates = []
    for path in root.rglob("RenderVideoReup*" + suffix):
        if not path.is_file() or not (path.parent / "_internal").is_dir():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if (suffix and path.suffix == ".exe") or (not suffix and magic == b"\x7fELF"):
            candidates.append(path)
    if len(candidates) != 1:
        raise ValueError("Gói cập nhật cần chứa một ứng dụng RenderVideoReup và thư mục _internal")
    executable = candidates[0]
    with executable.open("rb") as stream:
        magic = stream.read(4)
    if not (magic[:2] == b"MZ" if suffix else magic == b"\x7fELF"):
        raise ValueError("Gói cập nhật không đúng hệ điều hành")
    if not suffix:
        executable.chmod(0o755)
        for binary in (executable.parent / "_internal").rglob("*"):
            if binary.is_file() and binary.name in ("ffmpeg", "ffprobe"):
                binary.chmod(0o755)
    return [(executable, Path(job["entry"]).name), (executable.parent / "_internal", "_internal")]


def verify_windows_onefile(path: Path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("File cập nhật không phải EXE Windows")
        offset = struct.unpack_from("<I", header, 60)[0]
        if offset < 64 or offset > size - 24:
            raise ValueError("Header EXE cập nhật không hợp lệ")
        stream.seek(offset)
        pe = stream.read(6)
        if pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664:
            raise ValueError("Cần bản EXE Windows 64-bit")
        stream.seek(max(0, size - 4 * 1024 ** 2))
        if PYINSTALLER_COOKIE not in stream.read():
            raise ValueError("EXE cập nhật không phải bản standalone của ứng dụng")


class InstallTransaction:
    """Replace only program files; keep user files and a rollback copy."""
    def __init__(self, job: dict, entries: list, progress):
        self.base = Path(job["base_dir"]).resolve()
        self.workspace = Path(tempfile.mkdtemp(prefix=".reup-install-", dir=str(self.base)))
        self.entries = []
        self.applied = []
        try:
            for index, (source, name) in enumerate(entries):
                destination = _inside(self.base, self.base / name)
                if name in PROTECTED or destination.is_symlink():
                    raise ValueError("Không thể ghi đè dữ liệu người dùng hoặc liên kết tượng trưng")
                stage = self.workspace / f"new-{index}"
                progress(f"Đang chuẩn bị cài đặt: {name}", None)
                if source.is_dir():
                    count = [0]

                    def copy_file(src, dst):
                        result = shutil.copy2(src, dst)
                        count[0] += 1
                        progress(f"Đang chuẩn bị cài đặt: {count[0]} file", None)
                        return result

                    shutil.copytree(str(source), str(stage), copy_function=copy_file)
                else:
                    shutil.copy2(str(source), str(stage))
                self.entries.append((stage, destination, self.workspace / f"old-{index}"))
                progress(f"Đang chuẩn bị cài đặt: {index + 1}/{len(entries)} mục", (index + 1) / len(entries))
        except Exception:
            self.cleanup()
            raise

    def commit(self, progress):
        try:
            for index, (stage, destination, backup) in enumerate(self.entries):
                existed = destination.exists()
                if existed:
                    self._replace(destination, backup, progress)
                self.applied.append((destination, backup, existed))
                self._replace(stage, destination, progress)
                progress(f"Đang cài đặt: {index + 1}/{len(self.entries)} mục", (index + 1) / len(self.entries))
        except Exception:
            self.rollback()
            raise

    @staticmethod
    def _replace(source: Path, destination: Path, progress):
        deadline = time.monotonic() + FILE_RETRY_SECONDS
        while True:
            try:
                os.replace(str(source), str(destination))
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise PermissionError("File ứng dụng đang bị khóa. Hãy đóng các cửa sổ Render Video Reup khác rồi thử lại.")
                progress("Đang chờ hệ điều hành giải phóng file ứng dụng…", None)
                time.sleep(0.25)

    def rollback(self):
        # Pop each restored entry immediately: a second rollback after a locked
        # file must never delete a file that was already restored successfully.
        while self.applied:
            destination, backup, existed = self.applied[-1]
            if existed and not backup.exists():
                raise RuntimeError("Không tìm thấy bản sao cũ để khôi phục: " + str(backup))
            if destination.exists():
                _remove(self.base, destination)
            if existed:
                self._replace(backup, destination, lambda *args: None)
            self.applied.pop()

    def cleanup(self):
        _remove(self.base, self.workspace)


def launch_app(job: dict, marker: Path) -> subprocess.Popen:
    env = child_environment()
    env[READY_ENV] = str(marker)
    command = [job["python"], job["entry"]] if job["mode"] == "source" else [job["entry"]]
    with (Path(job["job_dir"]) / "startup.log").open("ab") as log:
        return subprocess.Popen(command, cwd=job["base_dir"], env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def wait_for_startup(process, marker: Path, version: str, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            data = json.loads(marker.read_text(encoding="utf-8"))
            if data.get("version") != version:
                raise RuntimeError("Ứng dụng mở lại không đúng phiên bản mới")
            return
        if process.poll() is not None:
            raise RuntimeError("Bản mới chưa mở được giao diện. Đang khôi phục bản cũ.")
        time.sleep(0.2)
    raise RuntimeError(f"Chưa xác nhận được bản mới sau {int(timeout)} giây. Đang khôi phục bản cũ.")


def stop_launched_app(process):
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        # Onefile has a bootloader parent and a Python child. Both must release
        # their handles before rollback; stop only this updater's own child tree.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15)
    else:
        process.terminate()
    process.wait(timeout=15)


def perform_update(job: dict, progress):
    archive = download(job, progress)
    entries = package_entries(archive, job, progress)
    transaction = InstallTransaction(job, entries, progress)
    process = None
    try:
        set_status(job, "waiting_parent")
        progress("Đang chờ ứng dụng lưu kết quả và đóng để cài bản mới…", None)
        deadline = time.monotonic() + 180
        while any(process_running(int(job.get(name, 0)))
                  for name in ("parent_pid", "parent_bootloader_pid")):
            if time.monotonic() > deadline:
                raise RuntimeError("Ứng dụng chưa đóng xong. Vui lòng thử lại sau khi dừng render.")
            time.sleep(0.25)
        transaction.commit(progress)
        progress("Đã cài đặt. Đang mở phiên bản mới, vui lòng chờ…", None)
        marker = Path(job["job_dir"]) / "app-ready.json"
        marker.unlink(missing_ok=True)
        process = launch_app(job, marker)
        wait_for_startup(process, marker, job["version"],
                         timeout=240 if job["mode"] == "onefile" else 90)
    except Exception as exc:
        progress("Cập nhật gặp lỗi. Đang khôi phục file của bản cũ…", None)
        if process is not None:
            stop_launched_app(process)
        changed = bool(transaction.applied)
        transaction.rollback()
        transaction.cleanup()
        if changed:
            raise RuntimeError(str(exc) + " Đã khôi phục bản cũ.") from exc
        raise
    try:
        transaction.cleanup()
    except OSError:
        progress("Bản mới đã mở. Bản sao cũ được giữ tại: " + str(transaction.workspace), 1.0)
    progress("Cập nhật thành công. Phiên bản mới đã mở.", 1.0)
    set_status(job, "done")
    _release_lock(job)


def acknowledge_startup(version: str):
    marker = os.environ.pop(READY_ENV, None)
    if marker:
        try:
            write_json(Path(marker), {"version": version})
        except OSError:
            pass
    # The helper can only be removed after its own executable/DLLs are closed.
    cleanup = threading.Timer(5 if marker else 0, cleanup_completed_jobs)
    cleanup.daemon = True
    cleanup.start()


def cleanup_completed_jobs():
    root = Path(tempfile.gettempdir()).resolve()
    for directory in root.glob("reup-update-*"):
        try:
            _inside(root, directory)
            job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
            status = read_status(directory)
            if (job.get("job_dir") != str(directory.resolve())
                    or status.get("state") not in ("done", "cancelled")
                    or "pid" not in status or process_running(int(status["pid"]))):
                continue
            _remove(root, directory)
        except (OSError, ValueError, RuntimeError):
            # A running/failed updater and its diagnostic files are kept.
            continue


def run_worker(job_file: Path):
    """Own Tk event loop; installing and waiting never block the progress window."""
    import tkinter as tk
    from tkinter import ttk
    job = json.loads(job_file.read_text(encoding="utf-8"))
    root = tk.Tk()
    root.title("Đang cập nhật Render Video Reup")
    # Kích thước cố định suốt quá trình (kể cả khi lỗi): đổi geometry làm cửa sổ giật.
    width, height = 580, 360
    root.geometry(f"{width}x{height}+{max(0, (root.winfo_screenwidth() - width) // 2)}"
                  f"+{max(0, (root.winfo_screenheight() - height) // 3)}")
    root.resizable(False, False)
    status = tk.StringVar(value="Đang bắt đầu cập nhật…")
    clock = tk.StringVar()
    ttk.Label(root, text="Cập nhật phiên bản " + job["version"], font=("", 16, "bold")).pack(pady=(24, 16))
    # Khung trạng thái cao cố định: thông báo 1 dòng hay nhiều dòng không đẩy thanh tiến trình lên/xuống.
    status_box = ttk.Frame(root, height=112, width=540)
    status_box.pack_propagate(False)
    status_box.pack(padx=20)
    ttk.Label(status_box, textvariable=status, wraplength=530, justify="center",
              anchor="center").pack(fill="both", expand=True)
    bar = ttk.Progressbar(root, maximum=1, length=520)
    bar.pack(pady=12)
    ttk.Label(root, text="Cửa sổ này vẫn mở khi ứng dụng chính đóng để thay file.").pack()
    ttk.Label(root, textvariable=clock).pack(pady=(4, 8))
    actions = ttk.Frame(root)
    actions.pack()
    events = queue.Queue()
    state = {"busy": True, "started": time.monotonic(), "last_report": 0.0, "phase": "",
             "bar_mode": ""}

    def show_progress(ratio):
        # Chỉ đổi chế độ khi thật sự khác: stop()/start() mỗi thông báo làm thanh nhảy về đầu.
        if ratio is None:
            if state["bar_mode"] != "indeterminate":
                bar.configure(mode="indeterminate", value=0)
                bar.start(15)
                state["bar_mode"] = "indeterminate"
            return
        if state["bar_mode"] != "determinate":
            bar.stop()
            bar.configure(mode="determinate")
            state["bar_mode"] = "determinate"
        bar.configure(value=ratio)

    def stop_progress(value=None):
        bar.stop()
        bar.configure(mode="determinate", value=bar["value"] if value is None else value)
        state["bar_mode"] = "determinate"

    def emit(message, ratio):
        now = time.monotonic()
        phase = message.split(":", 1)[0]
        if phase == state["phase"] and ratio != 1.0 and now - state["last_report"] < 0.1:
            return
        state["phase"] = phase
        state["last_report"] = now
        events.put(("progress", message, ratio))
        with (Path(job["job_dir"]) / "update.log").open("a", encoding="utf-8") as log:
            log.write(time.strftime("%H:%M:%S ") + message + "\n")

    def work():
        try:
            perform_update(job, emit)
            events.put(("done", "", None))
        except Exception as exc:
            import traceback
            with (Path(job["job_dir"]) / "update.log").open("a", encoding="utf-8") as log:
                log.write(traceback.format_exc())
            set_status(job, "error", str(exc))
            events.put(("error", str(exc), None))

    def start():
        if not root.winfo_viewable():
            root.wait_visibility()
        state["busy"] = True
        status.set("Đang bắt đầu cập nhật…")
        show_progress(None)
        retry.configure(state="disabled")
        close.configure(state="disabled")
        reopen.configure(state="disabled")
        set_status(job, "visible")
        threading.Thread(target=work, daemon=True).start()

    def reopen_previous():
        state["busy"] = True
        retry.configure(state="disabled")
        reopen.configure(state="disabled")
        close.configure(state="disabled")
        status.set("Đang mở lại phiên bản trước cập nhật…")

        def run():
            try:
                marker = Path(job["job_dir"]) / "previous-ready.json"
                marker.unlink(missing_ok=True)
                process = launch_app(job, marker)
                wait_for_startup(process, marker, job["previous_version"])
                events.put(("progress", "Đã mở lại phiên bản trước cập nhật.", 1.0))
                _release_lock(job)
                events.put(("done", "", None))
            except Exception as exc:
                events.put(("error", str(exc), None))

        threading.Thread(target=run, daemon=True).start()

    def finish():
        if state["busy"]:
            return
        if process_running(job["parent_pid"]):
            set_status(job, "cancelled")
        _release_lock(job)
        root.destroy()

    retry = ttk.Button(actions, text="Thử lại", command=start, state="disabled")
    retry.pack(side="left", padx=6)
    close = ttk.Button(actions, text="Đóng / Quay lại", command=finish, state="disabled")
    close.pack(side="left", padx=6)
    reopen = ttk.Button(actions, text="Mở lại bản cũ", command=reopen_previous, state="disabled")
    reopen.pack(side="left", padx=6)
    root.protocol("WM_DELETE_WINDOW", finish)

    def poll():
        text = f"Đã chạy {int(time.monotonic() - state['started'])} giây • Vui lòng giữ cửa sổ này mở"
        if clock.get() != text:
            clock.set(text)
        while True:
            try:
                kind, message, ratio = events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                if status.get() != message:
                    status.set(message)
                show_progress(ratio)
            elif kind == "error":
                state["busy"] = False
                stop_progress()
                status.set("Cập nhật chưa hoàn tất: " + message + "\nCó thể thử lại. Chi tiết: " + job["job_dir"])
                retry.configure(state="normal")
                close.configure(state="normal")
                if not process_running(job["parent_pid"]):
                    reopen.configure(state="normal")
            elif kind == "done":
                state["busy"] = False
                stop_progress(1)
                root.after(1800, root.destroy)
        root.after(100, poll)

    root.after(100, start)  # A visible window exists before the parent may close.
    root.after(100, poll)
    root.mainloop()


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--worker":
    run_worker(Path(sys.argv[2]))
