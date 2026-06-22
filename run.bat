@echo off
chcp 65001 >nul
set PYTHONUTF8=1
echo ========================================
echo  Video Reup Tool - Setup ^& Launch
echo ========================================

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python chua duoc cai dat!
    echo Tai tai: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install Python dependencies
echo.
echo [1/3] Kiem tra Python dependencies...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [ERROR] Cai dat dependencies that bai!
    pause
    exit /b 1
)

:: Download FFmpeg into bin/ if not present
if not exist "bin\ffmpeg.exe" (
    echo.
    echo [2/3] FFmpeg chua co - dang tai vao bin/ ...
    python setup_ffmpeg.py
    if errorlevel 1 (
        echo [ERROR] Tai FFmpeg that bai!
        echo        Thu chay lai hoac cai thu cong: winget install Gyan.FFmpeg
        pause
        exit /b 1
    )
) else (
    echo.
    echo [2/3] FFmpeg da co san trong bin/ - bo qua tai.
)

:: Launch app
echo.
echo [3/3] Khoi dong app...
python main.py

pause
