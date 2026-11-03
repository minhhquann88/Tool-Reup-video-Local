"""Real HTTP contract tests (Postman simulation) and headless GUI flow tests."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import edition
import license as license_mod
import main
import app_paths


@pytest.fixture
def simulated_license(tmp_path, monkeypatch):
    url = os.environ.get("LICENSE_TEST_API_URL")
    if not url:
        pytest.skip("Start Postman simulation and set LICENSE_TEST_API_URL; see docs/KIEM_TRA_PHIEN_BAN.md")
    monkeypatch.setattr(license_mod, "API_URL", url)
    monkeypatch.setattr(license_mod, "get_license_path", lambda: tmp_path / "license.json")
    monkeypatch.setattr(license_mod, "get_device_id", lambda: "test-device")
    return license_mod


@pytest.mark.parametrize("key", ["CURRENT", "OPTIONAL", "FORCED"])
@pytest.mark.parametrize("entry", ["activate", "check_license", "recheck_license"])
def test_version_response_survives_every_license_entry(simulated_license, key, entry):
    client = simulated_license
    if entry == "activate":
        result = client.activate(key)
    else:
        client.save_key(key)
        result = getattr(client, entry)()
    if entry == "recheck_license":
        assert result["status"] == client.RECHECK_VALID
    else:
        assert result["is_valid"]
    assert client.load_saved_key() == key
    if key == "CURRENT":
        assert result["update"] is None
    else:
        assert result["update"]["has_update"]
        assert result["update"]["force_update"] == (key == "FORCED")
        assert result["update"]["current_version"] == client.APP_VERSION
        assert result["update"]["download_url"].endswith(client.get_platform_name() + ".zip")


@pytest.mark.parametrize("key", ["SERVER_ERROR", "BAD_JSON", "BAD_BODY"])
def test_failed_server_response_keeps_saved_key(simulated_license, key):
    client = simulated_license
    client.save_key(key)
    result = client.recheck_license()
    assert result["status"] == client.RECHECK_NETERR
    assert result["update"] is None
    assert client.load_saved_key() == key


def test_revoked_key_is_removed(simulated_license):
    client = simulated_license
    client.save_key("INVALID")
    assert client.recheck_license()["status"] == client.RECHECK_INVALID
    assert client.load_saved_key() is None


def test_rejected_key_still_locks_app_if_disk_cannot_delete_it(simulated_license):
    client = simulated_license
    client.save_key("INVALID")
    with patch.object(app_paths, "delete_auth", side_effect=app_paths.StorageError("Không lưu được dữ liệu")):
        assert client.recheck_license()["status"] == client.RECHECK_INVALID


@pytest.mark.parametrize("old_key,new_key", [("FORCED", None), ("INVALID", "FORCED")])
def test_late_http_response_cannot_restore_or_delete_changed_key(simulated_license, old_key, new_key):
    client = simulated_license
    client.save_key(old_key)
    entered, proceed = threading.Event(), threading.Event()
    real_validate = client.validate_key

    def validate_after_change(key):
        entered.set()
        assert proceed.wait(5)
        return real_validate(key)  # Actual HTTP to the Postman dependency.

    with ThreadPoolExecutor(max_workers=1) as pool, \
            patch.object(client, "validate_key", side_effect=validate_after_change):
        future = pool.submit(client.recheck_license)
        try:
            assert entered.wait(5)
            if new_key is None:
                client.clear_key()
            else:
                client.save_key(new_key)
        finally:
            proceed.set()
        result = future.result(timeout=20)
    assert result["status"] == client.RECHECK_CHANGED
    assert client.load_saved_key() == new_key


@pytest.mark.parametrize("platform, expected", [("win32", "win"), ("darwin", "mac"), ("linux", "linux")])
def test_platform_names(monkeypatch, platform, expected):
    monkeypatch.setattr(license_mod.sys, "platform", platform)
    assert license_mod.get_platform_name() == expected


def _app():
    app = SimpleNamespace(
        _update_required=False, _license_locked=False, _update_dialog=None,
        _updater_active=False,
        _notified_versions=set(), _recheck_job="timer", _recheck_retries=0,
        _license_data={"key": "KEY"}, _processing=True,
        _cancel_event=threading.Event(), _login_cancel_flag=threading.Event(),
        _processor=Mock(), _batch_futures=[Mock()], _btn_process=Mock(),
        after=Mock(return_value="next-timer"), after_cancel=Mock(),
        withdraw=Mock(), _show_update_dialog=Mock(), _lock_ui_license=Mock(),
        _schedule_recheck=Mock(), winfo_exists=Mock(return_value=True), destroy=Mock(),
    )
    app._handle_update_response = lambda info, **kwargs: main.App._handle_update_response(app, info, **kwargs)
    return app


def _update(force=False):
    return {"has_update": True, "force_update": force, "latest_version": "1.1.0",
            "download_url": "https://downloads.example.com/app.zip", "changelog": "Sửa lỗi"}


def test_startup_forced_update_stops_tasks_hides_app_and_cancels_timer():
    app = _app()
    info = _update(force=True)
    app._handle_update_response(info, at_startup=True)
    assert app._update_required
    assert app._cancel_event.is_set()
    assert app._login_cancel_flag.is_set()
    app._processor.cancel_active.assert_called_once_with()
    app._batch_futures[0].cancel.assert_called_once_with()
    app.after_cancel.assert_called_once_with("timer")
    assert app._recheck_job is None
    app.withdraw.assert_called_once_with()
    app._btn_process.configure.assert_called_once_with(state="disabled")
    app._show_update_dialog.assert_called_once_with(info, True)


def test_startup_forced_update_is_not_suppressed_by_prior_optional_notice():
    app = _app()
    info = _update()
    app._handle_update_response(info)
    app._handle_update_response(info)
    app._show_update_dialog.assert_called_once_with(info, False)
    assert not app._update_required
    assert not app._cancel_event.is_set()
    app.withdraw.assert_not_called()
    app._processor.cancel_active.assert_not_called()
    old_dialog = app._update_dialog = Mock()
    app._handle_update_response(_update(force=True), at_startup=True)
    old_dialog.destroy.assert_called_once_with()
    assert app._update_required
    assert app._show_update_dialog.call_count == 2


@pytest.mark.parametrize("info", [None, [], {}, {"has_update": False}])
def test_absent_update_does_not_interrupt_app(info):
    app = _app()
    app._handle_update_response(info)
    app._show_update_dialog.assert_not_called()
    assert not app._cancel_event.is_set()


@pytest.mark.parametrize("processing", [False, True])
def test_recheck_defers_forced_update_and_keeps_key_timer(processing):
    app = _app()
    app._processing = processing
    for _ in range(3):
        main.App._on_recheck_result(app, {"status": license_mod.RECHECK_VALID,
                                        "data": {"key": "KEY"}, "update": _update(True)})
    assert not app._update_required
    assert not app._cancel_event.is_set()
    assert not app._login_cancel_flag.is_set()
    app.withdraw.assert_not_called()
    app._processor.cancel_active.assert_not_called()
    app._batch_futures[0].cancel.assert_not_called()
    app._btn_process.configure.assert_not_called()
    app._show_update_dialog.assert_not_called()
    app.after_cancel.assert_not_called()
    assert app.after.call_count == 3
    assert all(call.args == (main._RECHECK_SECONDS * 1000, app._schedule_recheck)
               for call in app.after.call_args_list)


def test_forced_response_without_startup_context_is_deferred():
    app = _app()
    app._handle_update_response(_update(True))
    assert not app._update_required
    app._show_update_dialog.assert_not_called()


def test_forced_version_is_enforced_on_next_open_with_real_recheck(simulated_license):
    client = simulated_license
    client.save_key("FORCED")
    running_app = _app()
    main.App._on_recheck_result(running_app, client.recheck_license())
    assert not running_app._update_required
    running_app._show_update_dialog.assert_not_called()
    running_app.after.assert_called_once_with(main._RECHECK_SECONDS * 1000, running_app._schedule_recheck)
    assert client.load_saved_key() == "FORCED"

    status = client.check_license()  # Fresh server check when the app opens again.
    assert status["is_valid"]
    reopened_app = _app()
    reopened_app._handle_update_response(status["update"], at_startup=True)
    assert reopened_app._update_required
    reopened_app.withdraw.assert_called_once_with()
    reopened_app._show_update_dialog.assert_called_once_with(status["update"], True)


def test_optional_recheck_keeps_regular_timer():
    app = _app()
    main.App._on_recheck_result(app, {"status": license_mod.RECHECK_VALID,
                                    "update": _update()})
    assert not app._update_required
    app.after.assert_called_once_with(main._RECHECK_SECONDS * 1000, app._schedule_recheck)


def test_network_rechecks_retry_then_return_to_normal_without_locking():
    app = _app()
    for _ in range(main._MAX_RETRIES):
        main.App._on_recheck_result(app, {"status": license_mod.RECHECK_NETERR})
        assert app.after.call_args.args[0] == main._RETRY_SECONDS * 1000
    main.App._on_recheck_result(app, {"status": license_mod.RECHECK_NETERR})
    assert app.after.call_args.args[0] == main._RECHECK_SECONDS * 1000
    assert app._recheck_retries == 0
    app._lock_ui_license.assert_not_called()


def test_invalid_recheck_still_locks_license():
    app = _app()
    main.App._on_recheck_result(app, {"status": license_mod.RECHECK_INVALID})
    app._lock_ui_license.assert_called_once()
    app.after.assert_not_called()


def test_changed_key_recheck_retries_without_locking_new_session():
    app = _app()
    main.App._on_recheck_result(app, {"status": license_mod.RECHECK_CHANGED})
    app._lock_ui_license.assert_not_called()
    app.after.assert_called_once_with(1000, app._schedule_recheck)


def test_storage_error_is_reported_without_treating_key_as_revoked():
    app = _app()
    app._log_msg = Mock()
    main.App._on_recheck_result(app, {"status": license_mod.RECHECK_STORAGE,
                                    "message": "Không ghi được dữ liệu"})
    app._log_msg.assert_called_once()
    app._lock_ui_license.assert_not_called()
    app.after.assert_called_once_with(main._RETRY_SECONDS * 1000, app._schedule_recheck)


def test_forced_update_blocks_new_batch_even_if_callback_is_invoked():
    app = _app()
    app._update_required = True
    main.App._start_processing(app)  # Returns before accessing video/settings widgets.


@pytest.mark.parametrize("force", [False, True])
def test_update_dialog_modality_and_close_behavior(force):
    app = _app()
    dlg = Mock()
    with patch.object(main.ctk, "CTkToplevel", return_value=dlg), \
            patch.object(main.ctk, "CTkLabel"), \
            patch.object(main.ctk, "CTkTextbox"), \
            patch.object(main.ctk, "CTkButton") as buttons:
        main.App._show_update_dialog(app, _update(force), force)
        if force:
            dlg.transient.assert_called_once_with("")
            dlg.grab_set.assert_called_once_with()
            assert buttons.call_count == 2
            # Chưa bấm cập nhật: đóng dialog bắt buộc = thoát ứng dụng.
            dlg.protocol.call_args.args[1]()
            dlg.destroy.assert_called_once_with()
            app.destroy.assert_called_once_with()
            assert app._update_dialog is None
        else:
            dlg.transient.assert_called_once_with(app)
            dlg.grab_set.assert_not_called()
            buttons.call_args.kwargs["command"]()
            dlg.destroy.assert_called_once_with()
            assert app._update_dialog is None


@pytest.mark.parametrize("force", [False, True])
def test_update_dialog_cannot_close_after_user_starts_update(force):
    app = _app()
    dlg = Mock()
    with patch.object(main.ctk, "CTkToplevel", return_value=dlg), \
            patch.object(main.ctk, "CTkLabel"), \
            patch.object(main.ctk, "CTkTextbox"), \
            patch.object(main.ctk, "CTkButton"):
        main.App._show_update_dialog(app, _update(force), force)
        app._updater_active = True
        dlg.protocol.call_args.args[1]()
    dlg.destroy.assert_not_called()
    app.destroy.assert_not_called()


def test_startup_passes_version_response_to_app():
    info = _update(force=True)
    status = {"is_valid": True, "data": {"key": "KEY"}, "update": info}
    with patch.object(main, "PROFILE", edition.PROFILES["pro"]), \
            patch.object(license_mod, "check_license", return_value=status), \
            patch.object(main, "App") as app_class:
        app_class.return_value._relaunch_license = False
        main.main()
    app_class.assert_called_once_with(license_data=status["data"], update_info=info)


def test_ui_version_matches_api_version():
    assert main.APP_VERSION == "v" + license_mod.APP_VERSION
