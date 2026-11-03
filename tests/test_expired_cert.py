"""Tải video khi máy chủ có chứng chỉ SSL hết hạn (vd v16.tokcdn.com)."""
import ssl
from unittest.mock import patch

import pytest
import requests
import urllib3

import core


def _cert_error(code, msg):
    """Dựng chuỗi lỗi giống requests thật: SSLError ← MaxRetryError ← ssl."""
    inner = ssl.SSLCertVerificationError(1, msg)
    inner.verify_code = code
    u3 = urllib3.exceptions.SSLError(inner)
    mre = urllib3.exceptions.MaxRetryError(None, "/x", u3)
    try:
        raise requests.exceptions.SSLError(mre) from mre
    except requests.exceptions.SSLError as e:
        return e


EXPIRED = lambda: _cert_error(10, "certificate has expired")
WRONG_HOST = lambda: _cert_error(62, "Hostname mismatch")


def test_detects_only_expired_cert():
    assert core._is_expired_cert_error(EXPIRED())
    assert not core._is_expired_cert_error(WRONG_HOST())
    assert not core._is_expired_cert_error(_cert_error(18, "self-signed"))
    assert not core._is_expired_cert_error(
        requests.exceptions.ConnectionError("reset"))


class _Resp:
    status_code = 200
    headers = {"Content-Type": "video/mp4", "content-length": "4096"}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield b"\0" * 4096

    def close(self):
        pass


def _run(first_error, tmp_path):
    calls = []

    def fake_get(url, **kw):
        calls.append(kw["verify"])
        if kw["verify"]:
            raise first_error
        return _Resp()

    d = core.VideoDownloader()
    logs = []
    with patch.object(d.session, "get", side_effect=fake_get), \
         patch.object(core, "probe_video", return_value={"duration": 1}), \
         patch.object(core.time, "sleep"):
        d.download("https://v16.tokcdn.com/a.mp4", str(tmp_path / "o.mp4"),
                   log=logs.append, retries=3)
    return calls, logs


def test_expired_cert_retries_without_verify(tmp_path):
    calls, logs = _run(EXPIRED(), tmp_path)
    assert calls == [True, False]
    assert any("Chứng chỉ SSL" in m for m in logs)


def test_other_cert_errors_keep_verifying(tmp_path):
    seen = []
    d = core.VideoDownloader()

    def fake_get(url, **kw):
        seen.append(kw["verify"])
        raise WRONG_HOST()

    with patch.object(d.session, "get", side_effect=fake_get), \
         patch.object(core.time, "sleep"):
        with pytest.raises(RuntimeError, match="Hostname mismatch"):
            d.download("https://x.test/a.mp4", str(tmp_path / "o.mp4"),
                       retries=3)
    assert seen == [True, True, True]
