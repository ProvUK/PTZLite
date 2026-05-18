@echo off
setlocal
echo ============================================
echo  PTZLite - Build
echo ============================================
echo.

rem Find Python — prefer explicit path, fall back to PATH
set "PYTHON="
if exist "C:\Python314\python.exe" set "PYTHON=C:\Python314\python.exe"
if "%PYTHON%"=="" where python >nul 2>&1 && set "PYTHON=python"
if "%PYTHON%"=="" (
    echo ERROR: Python not found. Install Python 3.11+ or set path in build.bat
    pause & exit /b 1
)
echo Using Python: %PYTHON%
echo.

echo [1/3] Installing dependencies...
"%PYTHON%" -m pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 ( echo pip failed & pause & exit /b 1 )

echo.
echo [2/3] Building with PyInstaller...
if exist "dist\PTZLite" rmdir /s /q "dist\PTZLite"
"%PYTHON%" -m PyInstaller ^
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
if errorlevel 1 ( echo PyInstaller failed & pause & exit /b 1 )
rem Note: config.yaml is NOT bundled (contains credentials).
rem       Inno Setup ships config.example.yaml as config.yaml for first-time installs.

echo.
echo [3/3] Building installer...

set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if "%ISCC%"=="" (
    echo.
    echo NOTE: Inno Setup 6 not found - skipping installer.
    echo Install it from https://jrsoftware.org/isinfo.php then re-run.
    echo.
    echo The raw build is at: dist\PTZLite\
    echo Run it directly: "dist\PTZLite\PTZLite.exe"
    pause & exit /b 0
)

"%ISCC%" installer.iss
if errorlevel 1 ( echo Inno Setup failed & pause & exit /b 1 )

echo.
echo ============================================
echo  Done!
echo    Installer : "PTZLite Setup.exe"
echo    Raw build : dist\PTZLite\
echo ============================================
pause
