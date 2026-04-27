"""Tests for the Android-facing Python analysis entrypoint."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANDROID_PY = os.path.join(ROOT, "android", "app", "src", "main", "python")
sys.path.insert(0, ANDROID_PY)
sys.path.insert(0, ROOT)

import watchpat_android


TESTDATA = os.path.join(ROOT, "tests", "testdata.dat")


class TestAndroidAnalysis(unittest.TestCase):
    def test_analyze_json_returns_summary_payload(self):
        payload = json.loads(watchpat_android.analyze_json(TESTDATA))

        self.assertIn("summary_text", payload)
        self.assertIn("summary", payload)
        self.assertEqual(payload["summary"]["packet_count"], 15)
        self.assertIn("mean_spo2", payload["summary"])
        self.assertIn("ahi", payload["summary"])


if __name__ == "__main__":
    unittest.main()
