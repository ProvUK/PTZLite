@echo off
rem Non-interactive dev build: refresh dist\PTZLite only (no pip, no pause, no installer).
rem Use build.bat when you want the full installer. Intended to be run after code changes.
setlocal
set "PYTHON=C:\Python314\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo [PTZLite devbuild] Rebuilding dist\PTZLite ...
if exist "dist\PTZLite" rmdir /s /q "dist\PTZLite"
"%PYTHON%" -m PyInstaller ^
    --noconfirm ^
    --noconsole ^
    --onedir ^
    --name "PTZLite" ^
    --icon "favicon.ico" ^
    --add-data "static;static" ^
    --add-binary "go2rtc.exe;bin" ^
    --hidden-import "pystray._win32" ^
    --hidden-import "PIL._imaging" ^
    --collect-all "uvicorn" ^
    --collect-all "starlette" ^
    --collect-all "fastapi" ^
    --collect-all "anyio" ^
    ptzlite.py
if errorlevel 1 ( echo [PTZLite devbuild] BUILD FAILED & exit /b 1 )
echo [PTZLite devbuild] Done: dist\PTZLite\PTZLite.exe
