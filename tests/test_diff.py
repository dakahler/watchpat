"""Tests for watchpat_diff.py."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import watchpat_diff


TESTDATA = os.path.join(os.path.dirname(__file__), "testdata.dat")


class TestWatchpatDiff(unittest.TestCase):
    def test_summarize_dat_file_reports_core_metrics(self):
        summary = watchpat_diff.summarize_dat_file(TESTDATA)

        self.assertEqual(summary.packet_count, 15)
        self.assertEqual(summary.duration_seconds, 15.0)
        self.assertGreater(summary.total_payload_bytes, 0)
        self.assertIn("PAT", summary.waveform_samples)
        self.assertIn("OxiA", summary.waveform_samples)
        self.assertIsNotNone(summary.metric_mean)

    def test_format_comparison_shows_zero_delta_for_same_file(self):
        left = watchpat_diff.summarize_dat_file(TESTDATA)
        right = watchpat_diff.summarize_dat_file(TESTDATA)

        rendered = watchpat_diff.format_comparison(left, right)

        self.assertIn("AHI (/hr)", rendered)
        self.assertIn("pAHI (/hr)", rendered)
        self.assertIn("pRDI (/hr)", rendered)
        self.assertIn("Waveform samples:", rendered)
        self.assertIn("Body position:", rendered)
        self.assertIn("+0.00", rendered)


if __name__ == "__main__":
    unittest.main()
