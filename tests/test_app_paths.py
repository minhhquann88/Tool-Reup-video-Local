import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import app_paths
import drive
import license as license_mod


TOKEN = {"refresh_token": "fake-refresh", "client_id": "fake-client",
         "client_secret": "fake-secret", "token": "fake-token"}


def _legacy(tmp_path, key=True, token=True):
    folder = tmp_path / "legacy"
    folder.mkdir(exist_ok=True)
    for name, data, enabled in (("license.json", {"key": "OLD-KEY"}, key),
                                ("token.json", TOKEN, token)):
        if enabled:
            (folder / name).write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_auth_paths_are_stable_for_source_and_moved_executables(tmp_path, monkeypatch):
    expected = license_mod.get_license_path(), drive.get_token_path()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    for folder in (tmp_path / "desktop", tmp_path / "read-only", tmp_path / "updated"):
        monkeypatch.setattr(sys, "executable", str(folder / "renamed.exe"))
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI123"), raising=False)
        assert (license_mod.get_license_path(), drive.get_token_path()) == expected
        assert expected[0].parent != folder


def test_pro_and_standard_have_separate_profiles():
    assert app_paths.get_data_dir("tool_reup_video_pro") != app_paths.get_data_dir("tool_reup_video")


@pytest.mark.parametrize("key,token", [(True, True), (True, False), (False, True)])
def test_migration_copies_each_legacy_file_once(tmp_path, key, token):
    legacy = _legacy(tmp_path, key, token)
    app_paths.migrate_legacy_auth()
    key_data, _ = app_paths.read_auth(license_mod.get_license_path())
    token_data, _ = app_paths.read_auth(drive.get_token_path())
    assert key_data == ({"key": "OLD-KEY"} if key else None)
    assert token_data == (TOKEN if token else None)
    assert (legacy / "license.json").exists() == key
    assert (legacy / "token.json").exists() == token


def test_existing_profile_wins_over_old_files(tmp_path):
    _legacy(tmp_path)
    path = license_mod.get_license_path()
    path.write_text('{"key":"NEW-KEY"}', encoding="utf-8")
    app_paths.migrate_legacy_auth()
    assert license_mod.load_saved_key() == "NEW-KEY"
    assert app_paths.read_auth(drive.get_token_path())[0] == TOKEN


def test_bad_legacy_json_does_not_block_valid_other_file(tmp_path):
    legacy = _legacy(tmp_path)
    (legacy / "license.json").write_text("{broken", encoding="utf-8")
    app_paths.migrate_legacy_auth()
    assert license_mod.load_saved_key() is None
    assert app_paths.read_auth(drive.get_token_path())[0] == TOKEN


def test_logout_and_deactivation_never_reimport_old_files(tmp_path):
    legacy = _legacy(tmp_path)
    assert license_mod.load_saved_key() == "OLD-KEY"
    license_mod.clear_key()
    drive.clear_credentials()
    app_paths.migrate_legacy_auth()
    assert license_mod.load_saved_key() is None
    assert app_paths.read_auth(drive.get_token_path())[0] is None
    assert (legacy / "license.json").exists() and (legacy / "token.json").exists()


def test_empty_first_launch_can_still_migrate_when_old_folder_is_found(tmp_path):
    assert license_mod.load_saved_key() is None
    _legacy(tmp_path)
    assert license_mod.load_saved_key() == "OLD-KEY"
    assert app_paths.read_auth(drive.get_token_path())[0] == TOKEN


def test_late_refresh_cannot_restore_a_logged_out_session(tmp_path):
    _legacy(tmp_path)
    path = drive.get_token_path()
    _, revision = app_paths.read_auth(path)
    drive.clear_credentials()
    assert not app_paths.write_auth(path, TOKEN, revision)
    assert app_paths.read_auth(path)[0] is None


def test_logout_also_invalidates_a_pending_first_login():
    path = drive.get_token_path()
    _, revision = app_paths.read_auth(path)
    drive.clear_credentials()
    assert not app_paths.write_auth(path, TOKEN, revision)


def test_failed_delete_keeps_logout_tombstone(tmp_path):
    _legacy(tmp_path)
    path = drive.get_token_path()
    app_paths.read_auth(path)
    unlink = Path.unlink

    def locked_token(target, *args, **kwargs):
        if target == path:
            raise PermissionError("locked")
        return unlink(target, *args, **kwargs)

    with patch.object(Path, "unlink", locked_token):
        with pytest.raises(app_paths.StorageError):
            drive.clear_credentials()
    assert path.exists()
    assert app_paths.read_auth(path)[0] is None


def test_write_failure_leaves_previous_json_intact():
    license_mod.save_key("OLD")
    with patch.object(app_paths.os, "replace", side_effect=PermissionError("disk error")):
        with pytest.raises(app_paths.StorageError, match="Không lưu được"):
            license_mod.save_key("NEW")
    assert license_mod.load_saved_key() == "OLD"
    assert not list(license_mod.get_license_path().parent.glob("*.tmp"))


def test_activation_does_not_report_success_if_save_fails():
    with patch.object(license_mod, "validate_key", return_value={"status": 1, "data": {"key": "NEW"}}), \
            patch.object(app_paths, "write_auth", side_effect=app_paths.StorageError("Không lưu được dữ liệu")):
        result = license_mod.activate("NEW")
    assert not result["is_valid"] and result["storage_error"]


def test_bad_state_file_is_not_reset_to_reimport_credentials(tmp_path):
    _legacy(tmp_path)
    (app_paths.get_data_dir() / ".auth-state.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(app_paths.StorageError, match="Trạng thái"):
        license_mod.load_saved_key()
    assert not license_mod.get_license_path().exists()


def test_drive_refresh_aborted_after_logout(tmp_path):
    _legacy(tmp_path)
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}", encoding="utf-8")
    creds = Mock(valid=False, expired=True, refresh_token="fake-refresh")
    creds.refresh.side_effect = lambda *_: drive.clear_credentials()
    creds.to_json.return_value = json.dumps(TOKEN)
    with patch.object(drive, "_secret_path", return_value=secret), \
            patch.object(drive.Credentials, "from_authorized_user_info", return_value=creds):
        with pytest.raises(RuntimeError, match="đã đăng xuất"):
            drive._get_credentials()
    assert app_paths.read_auth(drive.get_token_path())[0] is None


def test_processes_share_atomic_storage_lock(tmp_path):
    # Independent interpreters exercise the actual OS file lock and CAS, rather
    # than only the in-process threading lock. Only fake keys in a temp dir.
    path = tmp_path / "shared" / "license.json"
    app_paths.write_auth(path, {"key": "0"})
    script = """
import sys
from pathlib import Path
import app_paths
path = Path(sys.argv[1])
for _ in range(20):
    while True:
        data, revision = app_paths.read_auth(path)
        if app_paths.write_auth(path, {'key': str(int(data['key']) + 1)}, revision):
            break
"""
    children = [subprocess.Popen([sys.executable, "-c", script, str(path)],
                                 cwd=str(Path(__file__).resolve().parents[1]),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(3)]
    try:
        for process in children:
            out, err = process.communicate(timeout=30)
            assert process.returncode == 0, err.decode(errors="replace")
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert app_paths.read_auth(path)[0] == {"key": "60"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_private_permissions():
    license_mod.save_key("FAKE")
    assert app_paths.get_data_dir().stat().st_mode & 0o777 == 0o700
    assert license_mod.get_license_path().stat().st_mode & 0o777 == 0o600
