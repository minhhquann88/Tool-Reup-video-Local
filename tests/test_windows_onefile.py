import os
import struct
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import drive
import license as license_mod
import updater


def _exe(path):
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, 0x8664)
    path.write_bytes(bytes(data) + updater.PYINSTALLER_COOKIE + b"test archive")
    return path


def _job(tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    base = tmp_path / "app"
    base.mkdir()
    return {"job_dir": str(job_dir), "base_dir": str(base), "mode": "onefile",
            "entry": str(base / "RenderVideoReupPro_v1.0.1.exe"), "platform": "win"}


def test_onefile_update_accepts_direct_exe_and_keeps_shortcut_path(tmp_path):
    job = _job(tmp_path)
    exe = _exe(tmp_path / "release.part")
    assert updater.package_entries(exe, job, lambda *args: None) == [
        (exe, "RenderVideoReupPro_v1.0.1.exe")]


def test_onefile_does_not_accept_a_setup_executable(tmp_path):
    job = _job(tmp_path)
    exe = _exe(tmp_path / "release.part")
    exe.write_bytes(exe.read_bytes().replace(updater.PYINSTALLER_COOKIE, b"not-our-app"))
    with pytest.raises(ValueError, match="standalone"):
        updater.package_entries(exe, job, lambda *args: None)


def test_onefile_rejects_wrong_cpu_architecture(tmp_path):
    exe = _exe(tmp_path / "32bit.exe")
    data = bytearray(exe.read_bytes())
    struct.pack_into("<H", data, 68, 0x014c)
    exe.write_bytes(data)
    with pytest.raises(ValueError, match="64-bit"):
        updater.verify_windows_onefile(exe)


def test_onefile_helper_only_copies_the_executable(tmp_path):
    job = _job(tmp_path)
    _exe(Path(job["entry"]))
    with patch.object(updater.subprocess, "Popen", return_value=Mock(pid=1234)) as popen:
        updater.launch_helper(job, lambda *args: None)
    helper = Path(popen.call_args.args[0][0])
    assert helper.read_bytes() == Path(job["entry"]).read_bytes()
    assert not (helper.parent / "_internal").exists()
    assert popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows frozen runtime")
def test_onefile_detection_includes_bootloader_process(tmp_path, monkeypatch):
    exe = _exe(tmp_path / "RenderVideoReupPro.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI_test"), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    job = updater.create_job({"download_url": "https://example.com/release.exe", "latest_version": "1.1.0"})
    try:
        assert job["mode"] == "onefile"
        assert job["parent_bootloader_pid"] == os.getppid()
    finally:
        updater._release_lock(job)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runtime storage")
def test_read_only_exe_directory_uses_shared_user_profile_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "program-files" / "app.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    key_path = license_mod.get_license_path()
    token_path = drive.get_token_path()
    assert key_path.parent == token_path.parent
    assert (tmp_path / "profile") in key_path.parents
    assert key_path.parent.is_dir()


def test_self_test_dispatch_runs_without_license_or_network():
    import main
    with patch.object(sys, "argv", ["app.exe", "--self-test", "report.json"]), \
            patch("packaging_smoke.run", return_value=True) as run, \
            patch.object(license_mod, "check_license") as check:
        with pytest.raises(SystemExit) as exited:
            main.main()
    assert exited.value.code == 0
    run.assert_called_once_with(Path("report.json"))
    check.assert_not_called()
