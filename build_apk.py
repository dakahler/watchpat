#!/usr/bin/env python3
"""Build the WatchPAT Recorder debug APK via Gradle."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PROJECT_DIR = ROOT / "android"
APK_OUT = PROJECT_DIR / "app" / "build" / "outputs" / "apk" / "debug" / "watchpat-debug.apk"

# Gradle wrapper: .bat on Windows, shell script otherwise
GRADLEW = PROJECT_DIR / ("gradlew.bat" if sys.platform == "win32" else "gradlew")


def find_java_home() -> str | None:
    """Return JAVA_HOME from the environment, or None to let Gradle find it."""
    if "JAVA_HOME" in os.environ:
        return os.environ["JAVA_HOME"]
    # Windows default installed by Android Studio
    if sys.platform == "win32":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Android" / "openjdk" / "jdk-21.0.8"
        if candidate.exists():
            return str(candidate)
    return None


def main():
    print("=== WatchPAT Recorder — Build Debug APK ===")
    print(f"Project : {PROJECT_DIR}")

    if not GRADLEW.exists():
        sys.exit(f"ERROR: Gradle wrapper not found at {GRADLEW}")

    env = os.environ.copy()
    java_home = find_java_home()
    if java_home:
        env["JAVA_HOME"] = java_home
        print(f"JAVA_HOME: {java_home}")

    print()
    result = subprocess.run(
        [str(GRADLEW), "assembleDebug"],
        cwd=PROJECT_DIR,
        env=env,
    )

    if result.returncode != 0:
        print("\nBUILD FAILED")
        sys.exit(result.returncode)

    print(f"\nBUILD SUCCESSFUL\nAPK: {APK_OUT}")


if __name__ == "__main__":
    main()
