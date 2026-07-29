# -*- mode: python ; coding: utf-8 -*-
import re
from PyInstaller.utils.hooks import collect_all

try:
    import main
    import license
    base_name = 'RenderVideoReupPro' if getattr(license, 'APP_ID', '') == 'tool_reup_video_pro' else 'RenderVideoReup'
    app_name = f"{base_name}_{getattr(main, 'APP_VERSION', 'v1.0.0')}"
except Exception:
    app_name = "RenderVideoReup_v1.0.0"

datas = [('client_secret.json', '.'), ('bin', 'bin')]
binaries = []
hiddenimports = ['PIL._tkinter_finder', 'google.auth.transport.requests', 'google.oauth2.credentials', 'google_auth_oauthlib.flow', 'googleapiclient.discovery', 'googleapiclient.http', 'googleapiclient._helpers', 'openpyxl']
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=app_name,
)
