"""Exit non-zero unless the runtime loader resolves the patched libXft."""
import ctypes


xft = ctypes.CDLL("libXft.so.2")
xft.XftGetVersion.restype = ctypes.c_int
version = xft.XftGetVersion()
print(f"XftGetVersion={version}")
if version != 20308:
    raise SystemExit(f"Expected libXft 2.3.8 (20308), got {version}")
