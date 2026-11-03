# -*- mode: python ; coding: utf-8 -*-
import ast
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).parent
constants = {}
for node in ast.parse((root / 'license.py').read_text(encoding='utf-8')).body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
base = 'RenderVideoReupPro' if constants['APP_ID'] == 'tool_reup_video_pro' else 'RenderVideoReup'
name = base + '_v' + constants['APP_VERSION']

datas = [(str(root / file), '.') for file in ('client_secret.json', 'video.ico', 'video.png')]
datas += collect_data_files('customtkinter')
datas += collect_data_files('certifi')
datas += copy_metadata('google-api-python-client')
binaries = [(str(root / 'bin' / name), 'bin') for name in ('ffmpeg.exe', 'ffprobe.exe')]
for path in Path(sys.base_prefix).glob('vcruntime140*.dll'):
    binaries.append((str(path), '.'))

a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=['PIL._tkinter_finder', 'PIL.ImageTk', 'tkinter.ttk',
                   'google.auth.transport.requests', 'google.oauth2.credentials',
                   'google_auth_oauthlib.flow', 'googleapiclient.discovery',
                   'googleapiclient.http', 'googleapiclient._helpers', 'openpyxl'],
    hookspath=[], hooksconfig={},
    runtime_hooks=[str(root / 'packaging' / 'windows_runtime_hook.py')],
    excludes=['numpy', 'pandas', 'scipy', 'matplotlib', 'IPython', 'pytest'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name=name,
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(root / 'video.ico'), uac_admin=False,
)
