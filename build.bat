@echo off
setlocal
echo ============================================
echo  Reolink Viewer - Build
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 ( echo ERROR: Python not found in PATH & pause & exit /b 1 )

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 ( echo pip failed & pause & exit /b 1 )

echo.
echo [2/3] Building with PyInstaller...
if exist "dist\Reolink Viewer" rmdir /s /q "dist\Reolink Viewer"
python -m PyInstaller ^
    --noconsole ^
    --onedir ^
    --name "Reolink Viewer" ^
    --add-data "static;static" ^
    --hidden-import "pystray._win32" ^
    --hidden-import "PIL._tkinter_finder" ^
    reolink_viewer.py
if errorlevel 1 ( echo PyInstaller failed & pause & exit /b 1 )

echo Copying user files into dist...
copy /Y config.yaml "dist\Reolink Viewer\" >nul
copy /Y go2rtc.exe  "dist\Reolink Viewer\" >nul

echo.
echo [3/3] Building installer...

set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if "%ISCC%"=="" (
    echo.
    echo NOTE: Inno Setup 6 not found - skipping installer.
    echo Install it from https://jrsoftware.org/isinfo.php then re-run this script.
    echo.
    echo The raw build is still available at:
    echo   dist\Reolink Viewer\
    pause & exit /b 0
)

"%ISCC%" installer.iss
if errorlevel 1 ( echo Inno Setup failed & pause & exit /b 1 )

echo.
echo ============================================
echo  Done!
echo    Installer : Reolink Viewer Setup.exe
echo    Raw build : dist\Reolink Viewer\
echo ============================================
pause
