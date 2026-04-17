@echo off
setlocal

set "JAVA_HOME=C:\Program Files\Android\openjdk\jdk-21.0.8"
set "ANDROID_SDK=%USERPROFILE%\AppData\Local\Android\Sdk"
set "PROJECT_DIR=%~dp0android"
set "APK_OUT=%PROJECT_DIR%\app\build\outputs\apk\debug\watchpat-debug.apk"

echo === WatchPAT Recorder - Build Debug APK ===
echo Project : %PROJECT_DIR%
echo JAVA_HOME: %JAVA_HOME%
echo.

cd /d "%PROJECT_DIR%"
call gradlew.bat assembleDebug

if %ERRORLEVEL% neq 0 (
    echo.
    echo BUILD FAILED
    exit /b %ERRORLEVEL%
)

echo.
echo BUILD SUCCESSFUL
echo APK: %APK_OUT%
