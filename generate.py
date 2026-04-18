#!/usr/bin/env python3
"""
Regenerate Kaitai-derived parsers from watchpat.ksy.
Run this whenever watchpat.ksy changes.

Prerequisites:
  brew install kaitai-struct-compiler   (macOS)
  pip install kaitaistruct              (Python runtime)
  Android build.gradle already has: implementation 'io.kaitai:kaitai-struct-runtime:0.10'
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
KSY = ROOT / "watchpat.ksy"

PYTHON_OUT = ROOT / "kaitai" / "python"
JAVA_OUT   = ROOT / "kaitai" / "java"
ANDROID_OUT = (
    ROOT / "android" / "app" / "src" / "main" / "java"
    / "com" / "watchpat" / "recorder" / "kaitai"
)

JAVA_PACKAGE = "com.watchpat.recorder.kaitai"
GENERATED_JAVA = JAVA_OUT / "com" / "watchpat" / "recorder" / "kaitai" / "WatchpatPacket.java"


def find_ksc() -> str:
    ksc = shutil.which("kaitai-struct-compiler") or shutil.which("ksc")
    if ksc is None:
        sys.exit(
            "ERROR: kaitai-struct-compiler not found.\n"
            "  macOS:   brew install kaitai-struct-compiler\n"
            "  Other:   https://kaitai.io/#download"
        )
    return ksc


def run(cmd: list, **kwargs):
    print(" ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        sys.exit(f"Command failed with exit code {result.returncode}")


def main():
    ksc = find_ksc()

    PYTHON_OUT.mkdir(parents=True, exist_ok=True)
    JAVA_OUT.mkdir(parents=True, exist_ok=True)

    print("==> Generating Python parser")
    run([ksc, "--target", "python", "--outdir", str(PYTHON_OUT), str(KSY)])

    print("==> Generating Java parser")
    run([
        ksc,
        "--target", "java",
        "--java-package", JAVA_PACKAGE,
        "--outdir", str(JAVA_OUT),
        str(KSY),
    ])

    print("==> Copying Java parser into Android source tree")
    ANDROID_OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(GENERATED_JAVA, ANDROID_OUT / "WatchpatPacket.java")

    print("Done. Generated files:")
    print(f"  {PYTHON_OUT / 'watchpat_packet.py'}")
    print(f"  {GENERATED_JAVA}")
    print(f"  {ANDROID_OUT / 'WatchpatPacket.java'}")


if __name__ == "__main__":
    main()
