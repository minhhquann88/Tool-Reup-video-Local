import io
import json
import os
import stat
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import main
import updater
import app_paths
import drive
import license as license_mod


def _profile_snapshot():
    license_mod.save_key("FAKE-PROFILE-KEY")
    app_paths.write_auth(drive.get_token_path(), {"token": "fake-profile-token"})
    return {path: path.read_bytes() for path in (license_mod.get_license_path(), drive.get_token_path())}


def _job(tmp_path):
    base = tmp_path / "installed app"
    base.mkdir()
    job_dir = tmp_path / "update work"
    job_dir.mkdir()
    for name in ("license.json", "token.json", "client_secret.json", "my-video.mp4"):
        (base / name).write_text("user data", encoding="utf-8")
    (base / "main.py").write_text("# old program\n", encoding="utf-8")
    return {"job_dir": str(job_dir), "base_dir": str(base), "entry": str(base / "main.py"),
            "mode": "source", "version": "1.1.0", "previous_version": "1.0.1",
            "app_id": "tool_reup_video_pro", "parent_pid": 0, "python": sys.executable,
            "platform": "win", "sha256": ""}


@pytest.fixture
def release_job(tmp_path):
    api = os.environ.get("LICENSE_TEST_API_URL")
    if not api:
        pytest.skip("Start Postman simulation; see docs/KIEM_TRA_PHIEN_BAN.md")
    job = _job(tmp_path)
    job["url"] = api.rsplit("/api/validate-key", 1)[0] + "/releases/source.tar.gz"
    return job


def test_real_download_install_and_new_process_handshake(release_job):
    profile = _profile_snapshot()
    reports = []
    updater.perform_update(release_job, lambda text, ratio: reports.append((text, ratio)))
    base = Path(release_job["base_dir"])
    assert "REUP_UPDATE_READY" in (base / "main.py").read_text(encoding="utf-8")
    for name in ("license.json", "token.json", "client_secret.json", "my-video.mp4"):
        assert (base / name).read_text(encoding="utf-8") == "user data"
    assert updater.read_status(Path(release_job["job_dir"]))["state"] == "done"
    assert any("Đang tải" in text for text, _ in reports)
    assert any("Đang cài đặt" in text for text, _ in reports)
    assert reports[-1][1] == 1.0
    assert not list(base.glob(".reup-install-*"))
    assert all(path.read_bytes() == data for path, data in profile.items())
    assert all(base not in path.parents for path in profile)


def test_download_without_content_length_reports_indeterminate_progress(release_job):
    release_job["url"] = release_job["url"].rsplit("/", 1)[0] + "/unknown-size"
    reports = []
    file = updater.download(release_job, lambda text, ratio: reports.append((text, ratio)))
    assert file.stat().st_size > 0
    assert any("Đang tải" in text and ratio is None for text, ratio in reports)


@pytest.mark.parametrize("fault", ["fail", "corrupt", "checksum"])
def test_failed_package_never_changes_installed_program(release_job, fault):
    if fault == "checksum":
        release_job["sha256"] = "0" * 64
    else:
        release_job["url"] = release_job["url"].rsplit("/", 1)[0] + "/" + fault
    with pytest.raises(Exception):
        updater.perform_update(release_job, lambda *args: None)
    assert Path(release_job["entry"]).read_text(encoding="utf-8") == "# old program\n"


def test_failed_new_app_rolls_back_all_program_files(release_job):
    # Use the actual downloaded release and actual subprocess, but reject the
    # advertised version at the startup handshake to exercise rollback.
    real_launch = updater.launch_app
    profile = _profile_snapshot()

    def launch_with_wrong_version(job, marker):
        marker.write_text('{"version": "wrong"}', encoding="utf-8")
        return real_launch(job, Path(job["job_dir"]) / "unused-marker.json")

    with patch.object(updater, "launch_app", side_effect=launch_with_wrong_version):
        with pytest.raises(RuntimeError, match="Đã khôi phục bản cũ"):
            updater.perform_update(release_job, lambda *args: None)
    base = Path(release_job["base_dir"])
    assert (base / "main.py").read_text(encoding="utf-8") == "# old program\n"
    assert not (base / "core.py").exists()
    assert (base / "token.json").read_text(encoding="utf-8") == "user data"
    assert all(path.read_bytes() == data for path, data in profile.items())


@pytest.mark.parametrize("name", ["../escape", "/absolute/file", "C:/escape", "..\\escape"])
def test_zip_traversal_is_rejected(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(name, "malicious")
    with pytest.raises(ValueError):
        updater.extract_archive(archive, tmp_path / "extracted", lambda *args: None)
    assert not (tmp_path / "escape").exists()


def test_zip_symlink_is_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        package.writestr(info, "../../outside")
    with pytest.raises(ValueError, match="liên kết"):
        updater.extract_archive(archive, tmp_path / "extracted", lambda *args: None)


def test_linux_archive_library_links_are_materialized_safely(tmp_path):
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        info = tarfile.TarInfo("app/_internal/lib.so.1")
        info.size = 3
        package.addfile(info, io.BytesIO(b"lib"))
        link = tarfile.TarInfo("app/_internal/lib.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "lib.so.1"
        package.addfile(link)
    root = tmp_path / "extracted"
    updater.extract_archive(archive, root, lambda *args: None)
    assert (root / "app/_internal/lib.so").read_bytes() == b"lib"
    assert not (root / "app/_internal/lib.so").is_symlink()


def test_tar_external_link_is_rejected(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as package:
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside"
        package.addfile(link)
    with pytest.raises(ValueError):
        updater.extract_archive(archive, tmp_path / "extracted", lambda *args: None)


def test_linux_executable_with_version_dots_and_ffmpeg_permissions(tmp_path, monkeypatch):
    # Windows cannot expose POSIX execute bits; observe chmod while still
    # performing the real extraction and filesystem operations.
    permissions = {}
    chmod = Path.chmod

    def record_chmod(path, mode, *args, **kwargs):
        permissions[path] = mode
        return chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", record_chmod)
    job = _job(tmp_path)
    job.update(mode="frozen", platform="linux", entry=str(Path(job["base_dir"]) / "RenderVideoReupPro_v1.0.1"))
    archive = tmp_path / "linux.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("app/RenderVideoReupPro_v1.1.0", b"\x7fELF-program")
        package.writestr("app/_internal/bin/ffmpeg", b"\x7fELF-ffmpeg")
    entries = updater.package_entries(archive, job, lambda *args: None)
    assert entries[0][1] == "RenderVideoReupPro_v1.0.1"
    assert permissions[entries[0][0]] == 0o755
    assert permissions[entries[1][0] / "bin/ffmpeg"] == 0o755


def test_windows_release_keeps_existing_executable_name(tmp_path):
    job = _job(tmp_path)
    job.update(mode="frozen", entry=str(Path(job["base_dir"]) / "RenderVideoReupPro_v1.0.1.exe"))
    archive = tmp_path / "windows.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("app/RenderVideoReupPro_v1.1.0.exe", b"MZ-program")
        package.writestr("app/_internal/runtime.dll", b"runtime")
    entries = updater.package_entries(archive, job, lambda *args: None)
    assert entries[0][1] == "RenderVideoReupPro_v1.0.1.exe"
    assert entries[1][1] == "_internal"


def test_appimage_requires_direct_type2_image(tmp_path):
    job = _job(tmp_path)
    job.update(mode="appimage", entry=str(Path(job["base_dir"]) / "app.AppImage"))
    archive = tmp_path / "download"
    archive.write_bytes(b"<html>not an AppImage</html>")
    with pytest.raises(ValueError, match="AppImage"):
        updater.package_entries(archive, job, lambda *args: None)
    archive.write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02\x00rest")
    assert updater.package_entries(archive, job, lambda *args: None) == [(archive, "app.AppImage")]


def test_process_running_detects_this_process_without_terminating_it():
    assert updater.process_running(os.getpid())
    assert not updater.process_running(0)


def test_locked_file_during_commit_restores_already_replaced_files(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "FILE_RETRY_SECONDS", 0)
    job = _job(tmp_path)
    base = Path(job["base_dir"])
    (base / "core.py").write_text("old core", encoding="utf-8")
    source = tmp_path / "new.py"
    source.write_text("new program", encoding="utf-8")
    txn = updater.InstallTransaction(job, [(source, "main.py"), (source, "core.py")], lambda *args: None)
    replace = os.replace

    def fail_second_program(src, dst):
        if Path(src) == base / "core.py":
            raise PermissionError("File is locked by another instance")
        return replace(src, dst)

    with patch.object(updater.os, "replace", side_effect=fail_second_program):
        with pytest.raises(PermissionError):
            txn.commit(lambda *args: None)
    assert (base / "main.py").read_text(encoding="utf-8") == "# old program\n"
    assert (base / "core.py").read_text(encoding="utf-8") == "old core"
    txn.cleanup()


def test_installer_rejects_protected_user_files(tmp_path):
    job = _job(tmp_path)
    source = tmp_path / "new.json"
    source.write_text("not user data", encoding="utf-8")
    with pytest.raises(ValueError):
        updater.InstallTransaction(job, [(source, "token.json")], lambda *args: None)
    assert (Path(job["base_dir"]) / "token.json").read_text(encoding="utf-8") == "user data"


def test_rollback_can_resume_without_deleting_already_restored_files(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "FILE_RETRY_SECONDS", 0)
    job = _job(tmp_path)
    base = Path(job["base_dir"])
    (base / "core.py").write_text("old core", encoding="utf-8")
    source = tmp_path / "new.py"
    source.write_text("new program", encoding="utf-8")
    txn = updater.InstallTransaction(job, [(source, "main.py"), (source, "core.py")], lambda *args: None)
    txn.commit(lambda *args: None)
    replace = os.replace

    def locked_restore(src, dst):
        if Path(src) == txn.workspace / "old-0":
            raise PermissionError("temporarily locked")
        return replace(src, dst)

    with patch.object(updater.os, "replace", side_effect=locked_restore):
        with pytest.raises(PermissionError):
            txn.rollback()
    assert (base / "core.py").read_text(encoding="utf-8") == "old core"
    txn.rollback()
    assert (base / "main.py").read_text(encoding="utf-8") == "# old program\n"
    assert (base / "core.py").read_text(encoding="utf-8") == "old core"
    txn.cleanup()


def test_startup_timeout_is_an_error_and_never_reported_as_success(tmp_path):
    process = Mock()
    process.poll.return_value = None
    with pytest.raises(RuntimeError, match="khôi phục"):
        updater.wait_for_startup(process, tmp_path / "absent.json", "1.1.0", "tool_reup_video_pro",
                                 timeout=0)


# ── Không cài đè bản Pro lên bản thường (và ngược lại) ───────────────────────
def _ready(tmp_path, **data):
    marker = tmp_path / "ready.json"
    marker.write_text(json.dumps(data), encoding="utf-8")
    process = Mock()
    process.poll.return_value = None
    return process, marker


def test_startup_handshake_accepts_same_edition(tmp_path):
    process, marker = _ready(tmp_path, version="1.1.0", app_id="tool_reup_video")
    updater.wait_for_startup(process, marker, "1.1.0", "tool_reup_video", timeout=1)


@pytest.mark.parametrize("reported", [{"app_id": "tool_reup_video_pro"}, {}])
def test_startup_handshake_rejects_other_edition(tmp_path, reported):
    process, marker = _ready(tmp_path, version="1.1.0", **reported)
    with pytest.raises(RuntimeError, match="bản khác"):
        updater.wait_for_startup(process, marker, "1.1.0", "tool_reup_video", timeout=1)


def test_new_app_reports_its_app_id(tmp_path, monkeypatch):
    marker = tmp_path / "ready.json"
    monkeypatch.setenv(updater.READY_ENV, str(marker))
    monkeypatch.setattr(updater, "cleanup_completed_jobs", lambda: None)
    updater.acknowledge_startup("1.1.0")
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "version": "1.1.0", "app_id": license_mod.APP_ID}


def test_wrong_edition_link_is_rejected_before_download(tmp_path):
    job = _job(tmp_path)
    job.update(app_id="tool_reup_video", mode="onefile",
               url="https://example.com/v1.1.0/RenderVideoReupPro_v1.1.0.exe")
    with patch.object(updater, "download", side_effect=AssertionError("không được tải")):
        with pytest.raises(ValueError, match="bản khác"):
            updater.perform_update(job, lambda *args: None)
    assert Path(job["entry"]).read_text(encoding="utf-8") == "# old program\n"


@pytest.mark.parametrize("app_id,packaged", [
    ("tool_reup_video", "RenderVideoReupPro_v1.1.0.exe"),
    ("tool_reup_video_pro", "RenderVideoReup_v1.1.0.exe"),
])
def test_zip_with_other_edition_is_rejected(tmp_path, app_id, packaged):
    job = _job(tmp_path)
    job.update(app_id=app_id, mode="frozen", entry=str(Path(job["base_dir"]) / "App_v1.0.1.exe"))
    archive = tmp_path / "windows.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("app/" + packaged, b"MZ-program")
        package.writestr("app/_internal/runtime.dll", b"runtime")
    with pytest.raises(ValueError, match="bản khác"):
        updater.package_entries(archive, job, lambda *args: None)


def test_unnamed_or_matching_links_pass_the_name_check():
    job = {"app_id": "tool_reup_video"}
    for url in ("https://example.com/download?id=42", "https://example.com/setup.exe",
                "https://example.com/v1/RenderVideoReup_v1.1.0.exe",
                "https://example.com/v1/RenderVideoReup%5Fv1.1.0.AppImage"):
        updater._check_edition_name(updater._url_filename(url), job)


def test_helper_has_its_own_copy_of_frozen_runtime(tmp_path, monkeypatch):
    job = _job(tmp_path)
    base = Path(job["base_dir"])
    executable = base / "RenderVideoReupPro.exe"
    executable.write_bytes(b"MZ-app")
    (base / "_internal").mkdir()
    (base / "_internal/runtime.dll").write_bytes(b"runtime")
    job.update(mode="frozen", entry=str(executable))
    child = Mock(pid=1234)
    with patch.object(updater.subprocess, "Popen", return_value=child) as popen:
        updater.launch_helper(job, lambda *args: None)
    command = popen.call_args.args[0]
    copied = Path(command[0])
    assert copied.parent == Path(job["job_dir"]) / "helper"
    assert copied.read_bytes() == b"MZ-app"
    assert (copied.parent / "_internal/runtime.dll").read_bytes() == b"runtime"
    assert not (copied.parent / "token.json").exists()
    assert popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert command[1] == "--reup-updater"


def test_parent_does_not_close_before_independent_window_exists():
    app = SimpleNamespace(_update_dialog=Mock(), withdraw=Mock(), destroy=Mock(), after=Mock())
    process = Mock()
    process.poll.return_value = None
    job = {"job_dir": "unused"}
    with patch.object(updater, "read_status", return_value={}):
        main.App._monitor_updater(app, job, process, {})
    app.destroy.assert_not_called()
    app.withdraw.assert_not_called()
    app._update_dialog.destroy.assert_not_called()


def test_parent_waits_for_batch_to_save_before_exiting():
    thread = Mock()
    thread.is_alive.return_value = True
    dlg = Mock()
    app = SimpleNamespace(_batch_thread=thread, _update_dialog=dlg,
                          withdraw=Mock(), destroy=Mock(), after=Mock())
    process = Mock()
    process.poll.return_value = None
    with patch.object(updater, "read_status", return_value={"state": "waiting_parent"}):
        main.App._monitor_updater(app, {"job_dir": "unused"}, process, {})
        app.destroy.assert_not_called()
        dlg.destroy.assert_called_once_with()
        thread.is_alive.return_value = False
        main.App._monitor_updater(app, {"job_dir": "unused"}, process, {})
    app.destroy.assert_called_once_with()


def test_dead_helper_never_causes_parent_to_exit_on_stale_ready_status():
    app = SimpleNamespace(_batch_thread=None, _update_dialog=Mock(),
                          withdraw=Mock(), destroy=Mock(), after=Mock(),
                          _restore_update_prompt=Mock())
    process = Mock()
    process.poll.return_value = 1
    with patch.object(updater, "read_status", return_value={"state": "waiting_parent"}):
        main.App._monitor_updater(app, {"job_dir": "unused"}, process, {})
    app.destroy.assert_not_called()
    app.withdraw.assert_not_called()
    app._restore_update_prompt.assert_called_once()


def test_updater_dispatch_bypasses_license_and_single_instance_checks():
    with patch.object(main.sys, "argv", ["app", "--reup-updater", "job.json"]), \
            patch.object(updater, "run_worker") as worker, \
            patch.object(main.license_mod, "check_license") as check:
        main.main()
    worker.assert_called_once_with(Path("job.json"))
    check.assert_not_called()


def test_cleanup_only_removes_completed_inactive_updaters(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    for name, state, pid in (("finished", "done", 0), ("failed", "error", 0),
                             ("active", "done", os.getpid())):
        directory = tmp_path / ("reup-update-" + name)
        directory.mkdir()
        updater.write_json(directory / "job.json", {"job_dir": str(directory.resolve())})
        updater.write_json(directory / "status.json", {"state": state, "pid": pid})
    updater.cleanup_completed_jobs()
    assert not (tmp_path / "reup-update-finished").exists()
    assert (tmp_path / "reup-update-failed").exists()
    assert (tmp_path / "reup-update-active").exists()
