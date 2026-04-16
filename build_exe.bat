@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "APP_NAME=YouTubeBulkDownloader"
set "ENTRY_SCRIPT=youtube_downloader_gui.py"
set "FFMPEG_DIR=ffmpeg\bin"

if not exist "%ENTRY_SCRIPT%" (
    echo [ERROR] %ENTRY_SCRIPT% was not found in %cd%
    exit /b 1
)

if not exist "%FFMPEG_DIR%\ffmpeg.exe" (
    echo [ERROR] %FFMPEG_DIR%\ffmpeg.exe was not found.
    echo Make sure the local ffmpeg folder is present before building.
    exit /b 1
)

set "PY_CMD="
where py >nul 2>nul
if %errorlevel%==0 set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>nul
    if %errorlevel%==0 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo [ERROR] Python launcher not found. Install Python and make sure ^`py^` or ^`python^` is in PATH.
    exit /b 1
)

echo [1/4] Installing build dependencies...
call %PY_CMD% -m pip install --upgrade pip pyinstaller pyqt6
if errorlevel 1 exit /b 1

echo [2/4] Cleaning old build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

echo [3/4] Building one-file executable...
call %PY_CMD% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "%APP_NAME%" ^
  --hidden-import PyQt6.sip ^
  --hidden-import psutil ^
  --hidden-import yt_dlp ^
  --add-data "ffmpeg;ffmpeg" ^
  "%ENTRY_SCRIPT%"
if errorlevel 1 exit /b 1

echo [4/4] Done.
echo EXE created at: "%cd%\dist\%APP_NAME%.exe"
pause
exit /b 0
