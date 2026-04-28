@echo off
:: WatchPAT ONE Dashboard — Windows build script
:: Produces dist\WatchPAT\WatchPAT.exe

echo === WatchPAT Windows Build ===

:: Ensure PyInstaller is available
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
)

:: Clean previous build artefacts
if exist build\WatchPAT rmdir /s /q build\WatchPAT
if exist dist\WatchPAT  rmdir /s /q dist\WatchPAT

:: Run the build
python -m PyInstaller watchpat.spec

if errorlevel 1 (
    echo.
    echo BUILD FAILED — check output above.
    exit /b 1
)

echo.
echo === Build complete ===
echo Executable: dist\WatchPAT\WatchPAT.exe
echo.

:: Open the output folder in Explorer
explorer dist\WatchPAT
