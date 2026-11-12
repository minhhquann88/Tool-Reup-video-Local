"""Chia luồng CPU cho các FFmpeg chạy cùng lúc (hwinfo)."""
import os
import sys
from unittest.mock import patch

import pytest

import edition
import hwinfo


def _threads(slots):
    return [s.threads for s in slots]


@pytest.mark.parametrize("threads, workers, expected", [
    (4, 2, [0, 0]),            # máy ≤ 8 luồng: FFmpeg tự lấy như cũ (đo: chia cứng chậm 1–2%)
    (8, 2, [0, 0]),
    (8, 1, [0]),
    (16, 4, [4, 4, 4, 4]),
    (36, 6, [6] * 6),          # máy dev: 1 nhóm CPU 36 luồng, 6 encode
    (10, 4, [3, 3, 2, 2]),     # luồng dư chia cho các chỗ đầu
    (12, 16, [1] * 16),        # ít luồng hơn số encode: mỗi chỗ ít nhất 1
    (16, 1, [16]),             # bản khách: 1 encode dùng hết luồng
])
def test_threads_are_split_evenly(threads, workers, expected):
    slots = hwinfo.plan_encode_slots(threads, workers)
    assert _threads(slots) == expected
    assert [s.index for s in slots] == list(range(len(expected)))


def test_low_ram_reduces_encodes_but_never_below_one():
    assert len(hwinfo.plan_encode_slots(16, 4, ram_free_mb=1000)) == 2
    assert _threads(hwinfo.plan_encode_slots(16, 4, ram_free_mb=100)) == [16]
    assert _threads(hwinfo.plan_encode_slots(8, 2, ram_free_mb=100)) == [0]


def test_enough_ram_keeps_requested_encodes():
    assert len(hwinfo.plan_encode_slots(36, 6, ram_free_mb=2048)) == 6
    assert len(hwinfo.plan_encode_slots(36, 6, ram_free_mb=None)) == 6


def test_default_workers_follow_usable_threads_not_machine_total():
    pro = edition.PROFILES["pro"]
    # Máy 72 luồng nhưng tool chỉ dùng được 1 nhóm 36 luồng.
    assert edition.default_workers_for(pro, 36) == 6
    assert edition.default_workers_for(pro, 8) == 2
    assert edition.default_workers_for(edition.PROFILES["khach"], 36) == 1


def test_parse_cpulist():
    assert hwinfo._parse_cpulist("0-3,8,10-11\n") == {0, 1, 2, 3, 8, 10, 11}
    assert hwinfo._parse_cpulist("") == set()


def test_usable_threads_falls_back_to_cpu_count_on_error():
    hwinfo.usable_threads.cache_clear()
    name = "_windows_usable_threads" if sys.platform == "win32" else "_linux_usable_threads"
    try:
        with patch.object(hwinfo, name, side_effect=OSError("boom")):
            assert hwinfo.usable_threads() == (os.cpu_count() or 1)
    finally:
        hwinfo.usable_threads.cache_clear()


def test_usable_threads_on_this_machine_is_sane():
    hwinfo.usable_threads.cache_clear()
    try:
        n = hwinfo.usable_threads()
    finally:
        hwinfo.usable_threads.cache_clear()
    assert 1 <= n <= (os.cpu_count() or 1)
    ram = hwinfo.available_ram_mb()
    assert ram is None or ram > 0


@pytest.mark.skipif(not hasattr(os, "sched_getaffinity"), reason="Linux only")
def test_linux_skips_cpus_of_nodes_without_ram(tmp_path, monkeypatch):
    base = tmp_path / "node"
    for name, cpus, total in (("node0", "0-3", 8000000), ("node1", "4-7", 0)):
        (base / name).mkdir(parents=True)
        (base / name / "cpulist").write_text(cpus)
        (base / name / "meminfo").write_text(f"Node {name[4:]} MemTotal: {total} kB\n")
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(8)))
    real_listdir, real_join = os.listdir, os.path.join
    monkeypatch.setattr(hwinfo.os, "listdir",
                        lambda p: real_listdir(base) if p == "/sys/devices/system/node" else real_listdir(p))
    monkeypatch.setattr(hwinfo.os.path, "join",
                        lambda a, *rest: real_join(str(base) if a == "/sys/devices/system/node" else a, *rest))
    assert hwinfo._linux_usable_threads() == 4
