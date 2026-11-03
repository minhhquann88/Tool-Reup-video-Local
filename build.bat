@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

rem Cach dung:  build.bat            ^> ban Pro
rem             build.bat khach      ^> ban thuong (khach)
rem             build.bat all        ^> ca 2 ban
set "EDITIONS=%~1"
if "%EDITIONS%"=="" set "EDITIONS=pro"
if /i "%EDITIONS%"=="all" set "EDITIONS=pro khach"

echo ========================================
echo  Render Video Reup - ONE FILE EXE (%EDITIONS%)
echo ========================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Can cai Python 3.12 64-bit tren may build.
    pause
    exit /b 1
)

for %%E in (%EDITIONS%) do (
    echo.
    echo ---- Build ban %%E ----
    python build_windows.py --edition %%E
    if errorlevel 1 (
        echo [ERROR] Build hoac kiem tra EXE ban %%E that bai. Khong phan phoi ban nay.
        pause
        exit /b 1
    )
)
echo.
echo [OK] Moi ban chi co 1 file trong dist\:
echo      RenderVideoReupPro_v*.exe  = ban Pro
echo      RenderVideoReup_v*.exe     = ban thuong (khach)
echo Dung file nay de gui khach VA de dua len GitHub Release lam download_url cap nhat.
echo May nguoi dung khong can cai Python, pip hay FFmpeg.
pause
