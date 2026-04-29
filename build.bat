@echo off
setlocal
echo ============================================
echo  Reolink Viewer - Build
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    pause & exit /b 1
)

echo Installing / updating dependencies...
python -m pip install -r requirements.txt pyinstaller --quiet
if errorlevel 1 ( echo pip failed & pause & exit /b 1 )

echo.
echo Building executable...
python -m PyInstaller ^
    --noconsole ^
    --onedir ^
    --name "Reolink Viewer" ^
    --add-data "static;static" ^
    --hidden-import "pystray._win32" ^
    --hidden-import "PIL._tkinter_finder" ^
    app.py
if errorlevel 1 ( echo PyInstaller failed & pause & exit /b 1 )

echo.
echo Copying user files...
copy /Y config.yaml  "dist\Reolink Viewer\" >nul
copy /Y go2rtc.exe   "dist\Reolink Viewer\" >nul

echo.
echo ============================================
echo  Done!  Package ready at:
echo    dist\Reolink Viewer\
echo.
echo  To deploy to another PC, copy that folder.
echo  Edit config.yaml inside it before launching.
echo ============================================
pause
