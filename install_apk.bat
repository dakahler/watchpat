@echo off
setlocal

set "ADB=%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe"
set "APK=%~dp0android\app\build\outputs\apk\debug\watchpat-debug.apk"

echo === WatchPAT Recorder - Install APK via USB ===
echo ADB: %ADB%
echo APK: %APK%
echo.

if not exist "%ADB%" (
    echo ERROR: adb not found at %ADB%
    exit /b 1
)

if not exist "%APK%" (
    echo ERROR: APK not found. Run build_apk.bat first.
    exit /b 1
)

echo Checking for connected device...
"%ADB%" devices
echo.

"%ADB%" install -r "%APK%"

if %ERRORLEVEL% neq 0 (
    echo.
    echo INSTALL FAILED - ensure USB debugging is enabled and device is authorised
    exit /b %ERRORLEVEL%
)

echo.
echo INSTALL SUCCESSFUL
echo Launching app...
"%ADB%" shell am start -n com.watchpat.recorder/.MainActivity
