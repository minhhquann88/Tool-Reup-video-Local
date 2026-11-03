"""Per-user storage, one-time auth migration and short cross-process transactions."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

AUTH_FILES = ("license.json", "token.json")
_THREAD_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class StorageError(RuntimeError):
    pass


def _private_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            path.chmod(0o700)
        return path
    except OSError as exc:
        raise StorageError(f"Không tạo được thư mục dữ liệu: {path}") from exc


def get_data_dir(app_id: str | None = None) -> Path:
    import edition
    # app_id lạ → thư mục bản thường, như hành vi cũ.
    name = edition.PROFILES[edition.edition_for_app_id(app_id or edition.APP_ID) or "khach"]["base_name"]
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return _private_dir(root / name)


def auth_path(name: str) -> Path:
    if name not in AUTH_FILES:
        raise ValueError("Tên file xác thực không hợp lệ")
    return get_data_dir() / name


def legacy_dirs() -> list:
    if os.environ.get("APPIMAGE"):
        return [Path(os.environ["APPIMAGE"]).resolve().parent]
    if getattr(sys, "frozen", False):
        return [Path(sys.executable).resolve().parent]
    return [Path(__file__).resolve().parent]


def _cancelled(should_stop):
    if should_stop and should_stop():
        raise RuntimeError("Đã hủy thao tác dữ liệu đăng nhập")


@contextmanager
def storage_lock(directory: Path, should_stop=None, timeout=15):
    directory = _private_dir(directory)
    with _LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(str(directory.resolve()), threading.RLock())
    deadline = time.monotonic() + timeout
    while not thread_lock.acquire(timeout=0.1):
        _cancelled(should_stop)
        if time.monotonic() >= deadline:
            raise StorageError("Một cửa sổ khác đang xử lý dữ liệu đăng nhập. Vui lòng thử lại.")
    stream = None
    acquired = False
    try:
        _cancelled(should_stop)
        try:
            stream = (directory / ".auth.lock").open("a+b")
            if os.name != "nt":
                os.chmod(str(directory / ".auth.lock"), 0o600)
            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"\0")
                stream.flush()
            while True:
                _cancelled(should_stop)
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise StorageError("Một cửa sổ khác đang xử lý dữ liệu đăng nhập. Vui lòng thử lại.") from exc
                    time.sleep(0.05)
        except OSError as exc:
            raise StorageError(f"Không khóa được dữ liệu đăng nhập trong: {directory}") from exc
        yield
    finally:
        try:
            if stream is not None:
                try:
                    if acquired:
                        stream.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
        finally:
            thread_lock.release()


def _atomic_json(path: Path, value: dict):
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix="." + path.name + "-", suffix=".tmp", dir=str(path.parent))
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    except OSError as exc:
        raise StorageError(f"Không lưu được dữ liệu: {path}. Kiểm tra quyền ghi và dung lượng ổ đĩa.") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _raw(path: Path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StorageError(f"Không đọc được dữ liệu: {path}") from exc


def _json(raw):
    try:
        value = json.loads(raw.decode("utf-8-sig")) if raw is not None else None
        return value if isinstance(value, dict) else None
    except (ValueError, UnicodeError):
        return None


def _state(directory: Path) -> dict:
    path = directory / ".auth-state.json"
    raw = _raw(path)
    if raw is None:
        return {"schema": 1, "slots": {}}
    data = _json(raw)
    if not data or data.get("schema") != 1 or not isinstance(data.get("slots"), dict):
        raise StorageError(f"Trạng thái dữ liệu đăng nhập bị lỗi: {path}")
    if any(not isinstance(slot, dict) for slot in data["slots"].values()):
        raise StorageError(f"Trạng thái dữ liệu đăng nhập bị lỗi: {path}")
    return data


def _valid_legacy(name: str, data: dict | None) -> bool:
    if not data:
        return False
    required = ("key",) if name == "license.json" else ("refresh_token", "client_id", "client_secret")
    return all(isinstance(data.get(key), str) and data[key].strip() for key in required)


def migrate_legacy_auth():
    directory = get_data_dir()
    with storage_lock(directory):
        state = _state(directory)
        changed = False
        for name in AUTH_FILES:
            slot = state["slots"].get(name, {})
            if slot.get("migrated"):
                continue
            target = directory / name
            outcome = "existing" if target.exists() else "absent"
            if not target.exists():
                for parent in legacy_dirs():
                    old = parent / name
                    if old.resolve() == target.resolve() or not old.exists():
                        continue
                    data = _json(_raw(old))
                    if _valid_legacy(name, data):
                        _atomic_json(target, data)
                        outcome = "copied"
                        break
                    outcome = "invalid"
            new_slot = {"migrated": outcome != "absent", "epoch": slot.get("epoch") or uuid.uuid4().hex,
                        "deleted": False, "migration": outcome}
            if new_slot != slot:
                state["slots"][name] = new_slot
                changed = True
        if changed:
            _atomic_json(directory / ".auth-state.json", state)


def _prepare(path: Path):
    if path.name not in AUTH_FILES:
        raise ValueError("Tên file xác thực không hợp lệ")
    if path.parent.resolve() == get_data_dir().resolve():
        migrate_legacy_auth()


def _snapshot(path: Path, state: dict):
    slot = state["slots"].get(path.name, {})
    raw = _raw(path)
    if raw is not None and os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError as exc:
            raise StorageError(f"Không đặt được quyền riêng tư cho: {path}") from exc
    revision = (slot.get("epoch", ""), hashlib.sha256(raw or b"").hexdigest())
    return (None if slot.get("deleted") else _json(raw)), revision


def read_auth(path: Path, should_stop=None):
    _prepare(path)
    with storage_lock(path.parent, should_stop):
        return _snapshot(path, _state(path.parent))


def write_auth(path: Path, data: dict, expected_revision=None, should_stop=None) -> bool:
    _prepare(path)
    with storage_lock(path.parent, should_stop):
        state = _state(path.parent)
        _, revision = _snapshot(path, state)
        if expected_revision is not None and expected_revision != revision:
            return False
        _cancelled(should_stop)
        _atomic_json(path, data)
        slot = state["slots"].get(path.name, {})
        if not slot.get("migrated") or slot.get("deleted"):
            slot.update(migrated=True, deleted=False)
            slot.setdefault("epoch", uuid.uuid4().hex)
            state["slots"][path.name] = slot
            _atomic_json(path.parent / ".auth-state.json", state)
        return True


def delete_auth(path: Path, expected_revision=None) -> bool:
    _prepare(path)
    with storage_lock(path.parent):
        state = _state(path.parent)
        _, revision = _snapshot(path, state)
        if expected_revision is not None and expected_revision != revision:
            return False
        state["slots"][path.name] = {"migrated": True, "deleted": True, "epoch": uuid.uuid4().hex}
        # Persist the logout intent first, even if Windows refuses file deletion.
        _atomic_json(path.parent / ".auth-state.json", state)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"Không xóa được file đăng nhập: {path}") from exc
        return True
