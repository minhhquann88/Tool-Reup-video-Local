import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import edition
import updater

ROOT = Path(__file__).resolve().parent.parent


def _run(code, tmp_path, cwd=ROOT, **env):
    """Chạy trong tiến trình mới — edition được chọn đúng 1 lần lúc import."""
    full_env = dict(os.environ, LOCALAPPDATA=str(tmp_path / "localappdata"),
                    XDG_CONFIG_HOME=str(tmp_path / "config"))
    full_env.pop(edition.ENV_VAR, None)
    full_env.update(env)
    result = subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=full_env,
                            capture_output=True, text=True, timeout=60)
    return result


def _edition_of(tmp_path, cwd=ROOT, pre="", **env):
    result = _run(pre + "import edition; print(edition.EDITION)", tmp_path, cwd=cwd, **env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_profiles_have_distinct_identity():
    for key in ("app_id", "base_name", "title"):
        assert len({p[key] for p in edition.PROFILES.values()}) == len(edition.PROFILES)
    assert edition.PROFILES["khach"]["app_id"] == "tool_reup_video"
    assert edition.PROFILES["pro"]["app_id"] == "tool_reup_video_pro"


def test_source_defaults_to_pro_and_env_selects_standard(tmp_path):
    assert _edition_of(tmp_path) == "pro"
    assert _edition_of(tmp_path, REUP_EDITION="KHACH") == "khach"


def test_invalid_env_value_fails_loudly(tmp_path):
    result = _run("import edition", tmp_path, REUP_EDITION="vip")
    assert result.returncode != 0 and "REUP_EDITION" in result.stderr


def test_frozen_build_ignores_env(tmp_path):
    frozen = "import sys; sys.frozen = True; "
    assert _edition_of(tmp_path, pre=frozen, REUP_EDITION="khach") == "pro"


def test_stamp_wins_over_env_and_frozen(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy2(str(ROOT / "edition.py"), str(app / "edition.py"))
    (app / "_edition_stamp.py").write_text('EDITION = "khach"\n', encoding="utf-8")
    assert _edition_of(tmp_path, cwd=app, REUP_EDITION="pro") == "khach"
    assert _edition_of(tmp_path, cwd=app, pre="import sys; sys.frozen = True; ") == "khach"


@pytest.mark.parametrize("name", sorted(edition.PROFILES))
def test_every_module_follows_the_selected_edition(tmp_path, name):
    code = ("import json, app_paths, license, single_instance, main; "
            "print(json.dumps([license.APP_ID, app_paths.get_data_dir().name, "
            "single_instance._LOCK_NAME, main.APP_TITLE, main.PROFILE]))")
    result = _run(code, tmp_path, REUP_EDITION=name)
    assert result.returncode == 0, result.stderr
    app_id, data_dir, lock, title, main_profile = json.loads(result.stdout.strip().splitlines()[-1])
    profile = edition.PROFILES[name]
    assert app_id == profile["app_id"]
    assert data_dir == profile["base_name"]
    assert lock == profile["base_name"] + "_SingleInstance"
    assert title.startswith(profile["title"] + " v")
    assert main_profile == profile


def test_standard_edition_limits():
    # Bản thường: 1 video/lần, giãn 30 giây, không tải trước, upload xong mới
    # sang video kế, không cho chỉnh và không mở được 2 cửa sổ.
    khach = edition.PROFILES["khach"]
    assert (khach["max_workers"], khach["default_workers"]) == (1, 1)
    assert khach["default_delay"] == 30.0
    assert khach["prefetch"] == 0
    assert khach["upload_concurrency"] == 1
    assert not khach["split_upload_pool"]
    assert not khach["user_can_set_workers"]
    assert khach["single_instance"]


def test_profiles_share_the_same_keys():
    keys = [set(p) for p in edition.PROFILES.values()]
    assert all(k == keys[0] for k in keys)


def _source_package(tmp_path, version, stamp=None):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "license.py").write_text(
        f'import edition\nAPP_ID = edition.APP_ID\nAPP_VERSION = "{version}"\n', encoding="utf-8")
    if stamp:
        (root / "_edition_stamp.py").write_text(f'EDITION = "{stamp}"\n', encoding="utf-8")
    return root


@pytest.mark.parametrize("stamp,app_id,ok", [
    (None, "tool_reup_video_pro", True),
    ("khach", "tool_reup_video", True),
    (None, "tool_reup_video", False),        # bản thường không nhận gói Pro
    ("khach", "tool_reup_video_pro", False),  # bản Pro không nhận gói thường
])
def test_source_update_must_match_running_edition(tmp_path, stamp, app_id, ok):
    root = _source_package(tmp_path, "1.2.0", stamp)
    job = {"app_id": app_id, "version": "1.2.0"}
    if ok:
        updater._verify_source(root, job)
    else:
        with pytest.raises(ValueError):
            updater._verify_source(root, job)


def test_write_stamp_round_trips_and_rejects_unknown(tmp_path):
    path = edition.write_stamp(tmp_path / "stamp", "KHACH")
    assert path.read_text(encoding="utf-8") == 'EDITION = "khach"\n'
    shutil.copy2(str(ROOT / "edition.py"), str(path.parent / "edition.py"))
    assert _edition_of(tmp_path, cwd=path.parent) == "khach"
    with pytest.raises(ValueError):
        edition.write_stamp(tmp_path / "bad", "vip")


def test_edition_source_is_reported(tmp_path):
    result = _run("import edition; print(edition.EDITION, edition.EDITION_SOURCE)", tmp_path,
                  REUP_EDITION="khach")
    assert result.stdout.split() == ["khach", "env"]
    result = _run("import edition; print(edition.EDITION, edition.EDITION_SOURCE)", tmp_path)
    assert result.stdout.split() == ["pro", "default"]


def test_build_names_follow_requested_edition():
    import build_windows
    for name, profile in edition.PROFILES.items():
        values, exe = build_windows.release_constants(name)
        assert values["APP_ID"] == profile["app_id"]
        assert exe == f"{profile['base_name']}_v{values['APP_VERSION']}"


def test_cleaning_one_edition_keeps_the_other(tmp_path):
    import build_windows
    for file in ("RenderVideoReupPro_v1.0.6.exe", "RenderVideoReup_v1.0.6.exe", "RenderVideoReup-setup.exe"):
        (tmp_path / file).write_bytes(b"MZ")
    build_windows.clean_previous_outputs(tmp_path, "khach")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["RenderVideoReupPro_v1.0.6.exe"]
    build_windows.clean_previous_outputs(tmp_path, "pro")
    assert list(tmp_path.iterdir()) == []
