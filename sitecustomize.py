"""Test-safe Python startup customizations for the local repo.

Python imports `sitecustomize` automatically at startup when it is present on
`sys.path`. We use that hook to force a headless Matplotlib backend for the
unit test runner before any module can accidentally import the macOS GUI
backend and crash in AppKit.
"""

import os
import sys
import tempfile


def _running_unittest() -> bool:
    argv = sys.argv
    return (
        len(argv) >= 3
        and argv[0].endswith("python")
        and argv[1] == "-m"
        and argv[2] == "unittest"
    )


if _running_unittest():
    os.environ["MPLBACKEND"] = "Agg"
    os.environ.setdefault(
        "MPLCONFIGDIR",
        os.path.join(tempfile.gettempdir(), "watchpat-mpl"),
    )
