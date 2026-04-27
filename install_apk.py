#!/usr/bin/env python3
"""Install the WatchPAT Recorder APK on a connected Android device via ADB."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
APK = ROOT / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "watchpat-debug.apk"
APP_ACTIVITY = "com.watchpat.recorder/.MainActivity"


def find_adb() -> str:
    """Return path to adb, checking PATH then well-known SDK locations."""
    on_path = shutil.which("adb")
    if on_path:
        return on_path

    candidates: list[Path] = []
    home = Path.home()
    if sys.platform == "win32":
        candidates.append(home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    elif sys.platform == "darwin":
        candidates.append(home / "Library" / "Android" / "sdk" / "platform-tools" / "adb")
    else:
        candidates.append(home / "Android" / "Sdk" / "platform-tools" / "adb")

    for c in candidates:
        if c.exists():
            return str(c)

    sys.exit(
        "ERROR: adb not found. Add Android SDK platform-tools to PATH or install Android Studio."
    )


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd)
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result


def main():
    print("=== WatchPAT Recorder — Install APK via USB ===")

    adb = find_adb()
    print(f"ADB: {adb}")
    print(f"APK: {APK}")
    print()

    if not APK.exists():
        sys.exit("ERROR: APK not found. Run build_apk.py first.")

    print("Checking for connected device...")
    run([adb, "devices"])
    print()

    result = run([adb, "install", "-r", str(APK)], check=False)
    if result.returncode != 0:
        print("\nINSTALL FAILED — ensure USB debugging is enabled and device is authorised")
        sys.exit(result.returncode)

    print("\nINSTALL SUCCESSFUL")
    print("Launching app...")
    run([adb, "shell", "am", "start", "-n", APP_ACTIVITY])


if __name__ == "__main__":
    main()
