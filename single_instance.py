r"""
single_instance.py - Dam bao chi 1 instance cua app chay tai mot thoi diem.

Windows : Named Kernel Mutex ("Global\VideoReupTool_SingleInstance")
          - ton tai trong kernel memory, khong co file tren disk.
Linux   : Abstract Unix Domain Socket ("\0VideoReupTool_SingleInstance")
          - ton tai trong kernel namespace, khong co file tren disk.

Ca hai deu TU GIAI PHONG khi process ket thuc theo bat ky cach nao
(thoat binh thuong, crash, Task Manager kill, kill -9).

Nguoi dung KHONG THE bypass bang cach xoa file vi khong co file nao ton tai.
"""

import sys

_LOCK_NAME = "VideoReupTool_SingleInstance"

# -- Windows: Named Kernel Mutex -------------------------------------------------
if sys.platform == "win32":
    import ctypes

    _ERROR_ALREADY_EXISTS = 183
    _mutex_handle = None

    def acquire() -> bool:
        """
        Tao Named Kernel Mutex. Tra True neu day la instance dau tien,
        False neu da co instance khac dang giu mutex.
        """
        global _mutex_handle
        handle = ctypes.windll.kernel32.CreateMutexW(
            None,
            True,
            "Global\\" + _LOCK_NAME,
        )
        if not handle:
            return False

        err = ctypes.windll.kernel32.GetLastError()
        if err == _ERROR_ALREADY_EXISTS:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False

        _mutex_handle = handle
        return True

    def release() -> None:
        """Giai phong mutex khi thoat binh thuong."""
        global _mutex_handle
        if _mutex_handle:
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None

# -- Linux / macOS: Abstract Unix Domain Socket ----------------------------------
else:
    import socket as _socket

    _sock = None

    def acquire() -> bool:
        """
        Bind vao abstract Unix socket. Tra True neu bind thanh cong
        (instance dau tien), False neu da co instance khac dang bind.

        Luu y: prefix \0 (abstract namespace) - khong tao file nao tren disk,
        chi ton tai trong kernel. Bien mat ngay khi process dong.
        """
        global _sock
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
        try:
            # \0 prefix = abstract namespace (kernel-only, khong co file)
            s.bind("\0" + _LOCK_NAME)
            _sock = s
            return True
        except OSError:
            s.close()
            return False

    def release() -> None:
        """Giai phong socket khi thoat binh thuong."""
        global _sock
        if _sock:
            _sock.close()
            _sock = None
