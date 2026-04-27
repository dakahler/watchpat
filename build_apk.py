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


def find_java_home() -> str:
    """Return JAVA_HOME pointing at Java 17 or 21, or None to let Gradle choose."""
    if "JAVA_HOME" in os.environ:
        return os.environ["JAVA_HOME"]
    if sys.platform == "win32":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Android" / "openjdk" / "jdk-21.0.8"
        if candidate.exists():
            return str(candidate)
    elif sys.platform == "darwin":
        # Prefer LTS versions that Gradle supports (21, then 17)
        for version in ("21", "17"):
            result = subprocess.run(
                ["/usr/libexec/java_home", "-v", version],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                return result.stdout.strip()
    return None


def find_android_sdk() -> str:
    """Return the Android SDK path, checking env then well-known locations."""
    if "ANDROID_HOME" in os.environ:
        return os.environ["ANDROID_HOME"]
    if "ANDROID_SDK_ROOT" in os.environ:
        return os.environ["ANDROID_SDK_ROOT"]
    home = Path.home()
    candidates = []
    if sys.platform == "darwin":
        candidates.append(home / "Library" / "Android" / "sdk")
    elif sys.platform == "win32":
        candidates.append(home / "AppData" / "Local" / "Android" / "Sdk")
    else:
        candidates.append(home / "Android" / "Sdk")
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def ensure_debug_keystore():
    """Generate the standard Android debug keystore if it doesn't exist."""
    keystore = Path.home() / ".android" / "debug.keystore"
    if keystore.exists():
        return
    keystore.parent.mkdir(parents=True, exist_ok=True)
    print("Generating debug keystore...")
    result = subprocess.run([
        "keytool",
        "-genkey", "-v",
        "-keystore", str(keystore),
        "-storepass", "android",
        "-alias", "androiddebugkey",
        "-keypass", "android",
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-dname", "CN=Android Debug,O=Android,C=US",
    ])
    if result.returncode != 0:
        sys.exit("ERROR: Failed to generate debug keystore. Is 'keytool' on your PATH?")
    print(f"Debug keystore created at {keystore}")


def ensure_local_properties():
    """Write local.properties with sdk.dir if missing or sdk.dir not set."""
    local_props = PROJECT_DIR / "local.properties"
    sdk = find_android_sdk()
    if sdk is None:
        sys.exit(
            "ERROR: Android SDK not found. Install Android Studio or set ANDROID_HOME."
        )
    # Gradle requires forward slashes even on Windows
    sdk_dir = sdk.replace("\\", "/")
    if local_props.exists():
        content = local_props.read_text()
        if "sdk.dir" in content:
            return   # already configured
        local_props.write_text(content.rstrip() + f"\nsdk.dir={sdk_dir}\n")
    else:
        local_props.write_text(f"sdk.dir={sdk_dir}\n")
    print(f"sdk.dir: {sdk_dir}")


def main():
    print("=== WatchPAT Recorder — Build Debug APK ===")
    print(f"Project : {PROJECT_DIR}")

    if not GRADLEW.exists():
        sys.exit(f"ERROR: Gradle wrapper not found at {GRADLEW}")

    if sys.platform != "win32":
        GRADLEW.chmod(GRADLEW.stat().st_mode | 0o111)

    ensure_debug_keystore()
    ensure_local_properties()

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
