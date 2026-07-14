@echo off
chcp 65001 >nul
set PYTHONUTF8=1
echo ========================================
echo  Render Video Reup Pro - Build EXE
echo ========================================

:: Check client_secret.json exists before building
if not exist "client_secret.json" (
    echo [ERROR] Khong tim thay client_secret.json
    echo         Dat file nay vao thu muc project truoc khi build.
    pause
    exit /b 1
)

:: Check FFmpeg binaries exist
if not exist "bin\ffmpeg.exe" (
    echo [WARN] Chua co bin\ffmpeg.exe - chay setup_ffmpeg.py truoc...
    python setup_ffmpeg.py
    if errorlevel 1 (
        echo [ERROR] Khong tai duoc FFmpeg.
        pause
        exit /b 1
    )
)

:: Install PyInstaller
echo.
echo [1/3] Cai PyInstaller...
pip install pyinstaller -q

:: Build
echo.
echo [2/3] Dang dong goi...
pyinstaller --noconfirm --onedir --windowed ^
    --name "RenderVideoReupPro" ^
    --icon "video.ico" ^
    --add-data "client_secret.json;." ^
    --add-data "video.ico;." ^
    --add-data "bin;bin" ^
    --collect-all customtkinter ^
    --hidden-import "PIL._tkinter_finder" ^
    --hidden-import "google.auth.transport.requests" ^
    --hidden-import "google.oauth2.credentials" ^
    --hidden-import "google_auth_oauthlib.flow" ^
    --hidden-import "googleapiclient.discovery" ^
    --hidden-import "googleapiclient.http" ^
    --hidden-import "googleapiclient._helpers" ^
    --hidden-import "openpyxl" ^
    main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build that bai!
    pause
    exit /b 1
)

:: Remove token.json from dist if it sneaked in
if exist "dist\RenderVideoReupPro\token.json" (
    del /f /q "dist\RenderVideoReupPro\token.json"
    echo [INFO] Da xoa token.json khoi dist.
)

echo.
echo [3/3] Hoan thanh!
echo.
echo  App o: dist\RenderVideoReupPro\RenderVideoReupPro.exe
echo.
echo  Luu y:
echo    - token.json se duoc tao ben canh .exe sau khi dang nhap lan dau
echo    - Khong commit token.json len git
echo    - De phan phoi: zip toan bo thu muc dist\RenderVideoReupPro\

pause
