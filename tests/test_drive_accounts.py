"""Nhiều tài khoản Google Drive: thêm, chuyển, đăng xuất, token từ bản cũ."""
import json
from unittest.mock import MagicMock, patch

import pytest

import app_paths
import drive


def _token(name):
    return {"refresh_token": f"refresh-{name}", "client_id": "cid",
            "client_secret": "secret", "token": f"access-{name}"}


EMAILS = {f"refresh-{n}": f"{n}@gmail.com" for n in ("a", "b", "legacy")}


def _creds(name):
    creds = MagicMock()
    creds.to_json.return_value = json.dumps(_token(name))
    creds.refresh_token = f"refresh-{name}"
    return creds


@pytest.fixture
def env(tmp_path):
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}", encoding="utf-8")
    logins = []

    def login(_flow, _should_stop=None, on_auth_url=None):
        return _creds(logins.pop(0))

    with patch("drive._secret_path", return_value=secret), patch(
        "drive.InstalledAppFlow.from_client_secrets_file"
    ), patch("drive._run_oauth_login", side_effect=login), patch(
        "drive._fetch_email", side_effect=lambda c: EMAILS[c.refresh_token]
    ):
        yield logins


def _token_file():
    return app_paths.read_auth(drive.get_token_path())[0]


def _store():
    return drive._read_store()[0]


def test_add_accounts_and_switch_between_them(env):
    env.extend(["a", "b"])
    assert drive.add_account() == "a@gmail.com"
    assert _token_file() == _token("a")
    assert drive.add_account() == "b@gmail.com"
    assert _token_file() == _token("b")
    info = drive.list_accounts()
    assert info == {"accounts": ["a@gmail.com", "b@gmail.com"],
                    "active": "b@gmail.com", "has_token": True}

    drive.switch_account("a@gmail.com")
    assert _token_file() == _token("a")
    assert drive.list_accounts()["active"] == "a@gmail.com"


def test_switch_keeps_refreshed_token_of_previous_account(env):
    env.extend(["a", "b"])
    drive.add_account()
    drive.add_account()
    # Batch làm mới token của b (chỉ ghi token.json).
    refreshed = dict(_token("b"), token="access-b-new")
    app_paths.write_auth(drive.get_token_path(), refreshed)
    drive.switch_account("a@gmail.com")
    assert _store()["accounts"]["b@gmail.com"] == refreshed
    drive.switch_account("b@gmail.com")
    assert _token_file() == refreshed


def test_adding_same_email_again_replaces_it(env):
    env.extend(["a", "a"])
    drive.add_account()
    EMAILS["refresh-a"] = "A@gmail.com"
    try:
        drive.add_account()
    finally:
        EMAILS["refresh-a"] = "a@gmail.com"
    assert drive.list_accounts()["accounts"] == ["A@gmail.com"]


def test_remove_active_account_switches_to_remaining(env):
    env.extend(["a", "b"])
    drive.add_account()
    drive.add_account()
    assert drive.remove_account("b@gmail.com") == "a@gmail.com"
    assert _token_file() == _token("a")
    assert drive.list_accounts() == {"accounts": ["a@gmail.com"],
                                     "active": "a@gmail.com", "has_token": True}


def test_remove_inactive_account_keeps_current_token(env):
    env.extend(["a", "b"])
    drive.add_account()
    drive.add_account()
    assert drive.remove_account("a@gmail.com") == "b@gmail.com"
    assert _token_file() == _token("b")
    assert drive.list_accounts()["accounts"] == ["b@gmail.com"]


def test_remove_last_account_logs_out(env):
    env.append("a")
    drive.add_account()
    assert drive.remove_account("a@gmail.com") == ""
    assert _token_file() is None
    assert drive.list_accounts() == {"accounts": [], "active": "",
                                     "has_token": False}


def test_switch_to_unknown_account_fails_without_touching_token(env):
    env.append("a")
    drive.add_account()
    with pytest.raises(RuntimeError, match="Không tìm thấy"):
        drive.switch_account("x@gmail.com")
    assert _token_file() == _token("a")


def test_legacy_token_is_identified_and_kept_when_adding(env):
    app_paths.write_auth(drive.get_token_path(), _token("legacy"))
    assert drive.list_accounts() == {"accounts": [], "active": "",
                                     "has_token": True}
    env.append("a")
    drive.add_account()
    assert _store()["accounts"]["legacy@gmail.com"] == _token("legacy")
    drive.switch_account("legacy@gmail.com")
    assert _token_file() == _token("legacy")


def test_unidentifiable_legacy_token_is_not_overwritten(env):
    app_paths.write_auth(drive.get_token_path(), _token("legacy"))
    env.append("a")
    with patch("drive._fetch_email", side_effect=lambda c: (
            (_ for _ in ()).throw(OSError("offline"))
            if c.refresh_token == "refresh-legacy" else EMAILS[c.refresh_token])):
        with pytest.raises(RuntimeError, match="Không xác định được"):
            drive.add_account()
    assert _token_file() == _token("legacy")
    # Đăng xuất token chưa rõ email vẫn được.
    assert drive.remove_account("") == ""
    assert _token_file() is None


def test_current_account_email_records_legacy_token(env):
    app_paths.write_auth(drive.get_token_path(), _token("legacy"))
    with patch("drive._get_credentials", return_value=_creds("legacy")):
        assert drive.current_account_email() == "legacy@gmail.com"
    assert drive.list_accounts() == {"accounts": ["legacy@gmail.com"],
                                     "active": "legacy@gmail.com",
                                     "has_token": True}


def test_current_account_email_without_token_does_not_open_browser(env):
    assert drive.current_account_email() is None
    drive._run_oauth_login.assert_not_called()


def test_batch_login_clears_stale_active_email(env):
    env.extend(["a", "b"])
    drive.add_account()
    drive.clear_credentials()           # token mất, store vẫn ghi active = a
    drive._get_credentials()            # batch tự đăng nhập (chưa biết email)
    assert _token_file() == _token("b")
    assert drive.list_accounts()["active"] == ""
