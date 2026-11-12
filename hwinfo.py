"""
hwinfo.py — Đọc cấu hình máy người dùng (CPU, NUMA, RAM) và chia luồng cho FFmpeg.

Mục đích: mỗi FFmpeg chỉ dùng đúng phần luồng CPU của nó thay vì tự lấy hết
(-threads 0). Nhiều FFmpeg cùng tự lấy hết sẽ giành luồng của nhau: đo trên
máy 36 luồng chậm hơn ~18%; máy 4–16 luồng không chậm đi khi chia đều.

- "Luồng dùng được" = các luồng CPU mà tiến trình tool được phép chạy
  (Windows 10 với máy > 64 luồng chỉ cho 1 nhóm CPU), bỏ những CPU không có
  RAM gắn trực tiếp nếu đọc được (CPU đó lấy dữ liệu qua CPU khác, chậm gấp
  đôi khi tải nặng). Không đọc được thì giữ nguyên số luồng tiến trình thấy.
- Không thêm thư viện: Windows dùng ctypes, Linux đọc /sys và /proc.
- Mọi lỗi khi đọc đều trả về giá trị an toàn (hành vi cũ), không ném ra.
"""

from __future__ import annotations   # Python 3.8: cho phép 'X | None'

import os
import sys
from dataclasses import dataclass
from functools import lru_cache

# Ước lượng RAM 1 FFmpeg encode 1080x1920 cần (đo: ~130–220MB khi giới hạn
# luồng) + phần chừa cho hệ thống. Chỉ dùng để không mở quá nhiều FFmpeg khi
# máy sắp hết RAM (thiếu RAM → Windows đẩy xuống đĩa, chậm hẳn).
_ENCODE_BASE_MB = 120
_ENCODE_PER_THREAD_MB = 15
_RAM_RESERVE_MB = 512

# Chỉ chia luồng cố định khi máy có nhiều hơn số này (xem plan_encode_slots).
SPLIT_MIN_THREADS = 8


@dataclass(frozen=True)
class Slot:
    """Một chỗ encode: FFmpeg chạy ở chỗ này dùng `threads` luồng."""
    index: int
    threads: int


# ── Đọc cấu hình máy ─────────────────────────────────────────────────────────

def _popcount(mask: int) -> int:
    return bin(mask).count("1")


def _windows_usable_threads() -> int | None:
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class GroupAffinity(ctypes.Structure):
        _fields_ = [("Mask", ctypes.c_size_t), ("Group", wt.WORD),
                    ("Reserved", wt.WORD * 3)]

    # Khai báo kiểu: không có thì handle giả -1 bị cắt còn 32-bit trên Windows
    # 64-bit → "invalid handle" và mọi lời gọi thất bại.
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.GetProcessGroupAffinity.argtypes = [wt.HANDLE, ctypes.POINTER(wt.USHORT),
                                            ctypes.POINTER(wt.USHORT)]
    k32.GetProcessAffinityMask.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                           ctypes.POINTER(ctypes.c_size_t)]
    k32.GetNumaHighestNodeNumber.argtypes = [ctypes.POINTER(wt.ULONG)]
    k32.GetNumaNodeProcessorMaskEx.argtypes = [wt.USHORT, ctypes.POINTER(GroupAffinity)]
    k32.GetNumaAvailableMemoryNodeEx.argtypes = [wt.USHORT, ctypes.POINTER(ctypes.c_ulonglong)]
    proc = k32.GetCurrentProcess()
    # Nhóm CPU của tiến trình (Windows 10: chỉ 1 nhóm, tối đa 64 luồng).
    count = wt.USHORT(4)
    groups = (wt.USHORT * 4)()
    if not k32.GetProcessGroupAffinity(proc, ctypes.byref(count), groups):
        return None
    my_groups = set(groups[:count.value])
    proc_mask = ctypes.c_size_t()
    sys_mask = ctypes.c_size_t()
    if not k32.GetProcessAffinityMask(proc, ctypes.byref(proc_mask), ctypes.byref(sys_mask)):
        return None
    if len(my_groups) != 1 or not proc_mask.value:
        return None   # tiến trình trải nhiều nhóm (Windows 11): để os.cpu_count()
    group = next(iter(my_groups))
    allowed = proc_mask.value

    # Bỏ các CPU thuộc NUMA node không có RAM gắn trực tiếp.
    highest = wt.ULONG()
    if not k32.GetNumaHighestNodeNumber(ctypes.byref(highest)):
        return _popcount(allowed)
    with_ram = 0
    for node in range(highest.value + 1):
        ga = GroupAffinity()
        if not k32.GetNumaNodeProcessorMaskEx(wt.USHORT(node), ctypes.byref(ga)):
            continue
        if ga.Group != group:
            continue
        mem = ctypes.c_ulonglong()
        if k32.GetNumaAvailableMemoryNodeEx(wt.USHORT(node), ctypes.byref(mem)) and mem.value:
            with_ram |= ga.Mask
    usable = allowed & with_ram
    return _popcount(usable) if usable else _popcount(allowed)


def _parse_cpulist(text: str) -> set:
    cpus = set()
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            cpus.update(range(int(lo), int(hi) + 1))
        else:
            cpus.add(int(part))
    return cpus


def _linux_usable_threads() -> int | None:
    try:
        allowed = set(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None
    base = "/sys/devices/system/node"
    with_ram = set()
    try:
        for name in os.listdir(base):
            if not (name.startswith("node") and name[4:].isdigit()):
                continue
            with open(os.path.join(base, name, "meminfo"), encoding="ascii") as fh:
                total_kb = next((int(line.split()[-2]) for line in fh
                                 if "MemTotal:" in line), 0)
            if total_kb:
                with open(os.path.join(base, name, "cpulist"), encoding="ascii") as fh:
                    with_ram |= _parse_cpulist(fh.read())
    except (OSError, ValueError, IndexError):
        with_ram = set()
    usable = allowed & with_ram
    return len(usable) if usable else len(allowed)


@lru_cache(maxsize=1)
def usable_threads() -> int:
    """Số luồng CPU tool dùng được cho FFmpeg (≥ 1). Cache: cấu hình CPU không đổi."""
    try:
        if sys.platform == "win32":
            n = _windows_usable_threads()
        else:
            n = _linux_usable_threads()
    except Exception:   # noqa: BLE001 — không bao giờ làm hỏng batch vì dò máy
        n = None
    return max(1, n or os.cpu_count() or 1)


def available_ram_mb() -> float | None:
    """RAM còn dùng được (MB) lúc gọi; None nếu không đọc được."""
    try:
        if sys.platform == "win32":
            import ctypes
            import ctypes.wintypes as wt

            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemStatus()
            status.dwLength = ctypes.sizeof(MemStatus)
            if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.ullAvailPhys / 2**20
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024
    except Exception:   # noqa: BLE001
        return None
    return None


# ── Chia luồng ───────────────────────────────────────────────────────────────

def _encode_ram_mb(workers: int, threads: int) -> float:
    return workers * (_ENCODE_BASE_MB + _ENCODE_PER_THREAD_MB * threads)


def plan_encode_slots(threads: int, workers: int,
                      ram_free_mb: float | None = None) -> list:
    """Chia `threads` luồng CPU cho tối đa `workers` FFmpeg chạy cùng lúc.

    Số FFmpeg giảm bớt nếu RAM trống không đủ cho từng ấy encode. Luồng dư
    (threads % workers) chia thêm 1 cho các chỗ đầu. Luôn trả ≥ 1 chỗ.

    Máy ≤ SPLIT_MIN_THREADS luồng: threads = 0 (FFmpeg tự lấy, như cũ) —
    đo trên máy 4 và 8 luồng, chia cứng chậm hơn 1–2%; máy 16 luồng nhanh hơn
    ~4%, 36 luồng ~8%.
    """
    threads = max(1, int(threads))
    workers = max(1, int(workers))
    if ram_free_mb is not None:
        budget = ram_free_mb - _RAM_RESERVE_MB
        while workers > 1 and _encode_ram_mb(workers, max(1, threads // workers)) > budget:
            workers -= 1
    if threads <= SPLIT_MIN_THREADS:
        return [Slot(index=i, threads=0) for i in range(workers)]
    base, extra = divmod(threads, workers)
    return [Slot(index=i, threads=max(1, base + (1 if i < extra else 0)))
            for i in range(workers)]
