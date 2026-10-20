import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _isolate_error_log(tmp_path, monkeypatch):
    """Test không được ghi app_error.log / crash.log thật của người dùng."""
    import main
    monkeypatch.setattr(main, "_crash_dir", lambda: tmp_path)
