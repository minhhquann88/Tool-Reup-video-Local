import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _isolate_error_log(tmp_path, monkeypatch):
    """No test may read/migrate/write the user's real auth data or logs."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    import app_paths
    monkeypatch.setattr(app_paths, "legacy_dirs", lambda: [tmp_path / "legacy"])
    import main
    monkeypatch.setattr(main, "_crash_dir", lambda: tmp_path)
