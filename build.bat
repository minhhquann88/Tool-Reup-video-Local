@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

echo ========================================
echo  Render Video Reup Pro - ONE FILE EXE
echo ========================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Can cai Python 3.12 64-bit tren may build.
    pause
    exit /b 1
)

python build_windows.py
if errorlevel 1 (
    echo [ERROR] Build hoac kiem tra EXE that bai. Khong phan phoi ban nay.
    pause
    exit /b 1
)
echo.
echo [OK] Chi co 1 file: dist\RenderVideoReup*_v*.exe
echo Dung file nay de gui khach VA de dua len GitHub Release lam download_url cap nhat.
echo May nguoi dung khong can cai Python, pip hay FFmpeg.
pause
