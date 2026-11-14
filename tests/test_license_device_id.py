"""Mã thiết bị gửi lên server bản quyền phải ổn định giữa các lần mở app.

Trên Linux, uuid.getnode() (MAC) không ổn định: libuuid lấy MAC của card mạng
đầu tiên (docker/VPN/Wi-Fi MAC ngẫu nhiên) hoặc sinh số ngẫu nhiên mỗi tiến
trình. Mã thiết bị đổi → server từ chối key cũ sau khi mở lại app.
"""
import random

import pytest

import license as license_mod

MID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def linux(monkeypatch, tmp_path):
    monkeypatch.setattr(license_mod.sys, "platform", "linux")
    etc, dbus = tmp_path / "machine-id", tmp_path / "dbus-machine-id"
    monkeypatch.setattr(license_mod, "_LINUX_MACHINE_ID_PATHS", (etc, dbus))
    # Mỗi "lần mở app" uuid.getnode() có thể trả MAC khác (ngẫu nhiên).
    monkeypatch.setattr(license_mod.uuid, "getnode",
                        lambda: random.getrandbits(48) | (1 << 40))
    return etc, dbus


def test_linux_device_id_stable_across_restarts(linux):
    etc, _ = linux
    etc.write_text(MID + "\n")
    ids = {license_mod.get_device_id() for _ in range(5)}
    assert len(ids) == 1
    dev = ids.pop()
    assert dev.startswith("lnx_")
    assert MID not in dev                 # không lộ machine-id gốc


def test_linux_falls_back_to_dbus_machine_id(linux):
    etc, dbus = linux
    etc.write_text("uninitialized\n")
    dbus.write_text(MID + "\n")
    first = license_mod.get_device_id()
    etc.write_text(MID + "\n")
    assert first == license_mod.get_device_id()


@pytest.mark.parametrize("content", ["", "uninitialized", "0" * 32, "xyz"])
def test_linux_invalid_machine_id_falls_back_to_mac(linux, content):
    etc, _ = linux
    etc.write_text(content)
    assert license_mod.get_device_id().startswith("mac_")


def test_windows_device_id_unchanged(monkeypatch):
    winreg = pytest.importorskip("winreg")
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
        guid, _ = winreg.QueryValueEx(k, "MachineGuid")
    assert license_mod.get_device_id() == "win_" + str(guid).strip().strip("{}").lower()
