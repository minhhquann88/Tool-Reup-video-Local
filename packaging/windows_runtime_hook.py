"""Windowed builds have no console streams; keep startup errors diagnosable."""
import os
import sys
from pathlib import Path

if sys.platform == 'win32':
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    if sys.stderr is None:
        try:
            import edition
            directory = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / edition.BASE_NAME
            directory.mkdir(parents=True, exist_ok=True)
            sys.stderr = open(directory / 'startup.log', 'a', encoding='utf-8', errors='backslashreplace')
        except OSError:
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')
