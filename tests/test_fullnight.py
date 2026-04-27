"""Integration tests for the full-night recording in tests/testdata2.dat.

testdata2.dat is ~12 MB of real WatchPAT ONE data covering approximately
6 hours of sleep.  These tests verify end-to-end parsing, CRC integrity,
and that the derived clinical metrics land in medically meaningful ranges.
"""

import math
import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from watchpat_diff import summarize_dat_file
from watchpat_protocol import verify_crc

TESTDATA2 = os.path.join(os.path.dirname(__file__), "testdata2.dat")

# Derived once for all tests to avoid re-parsing 21 k packets per test case.
_SUMMARY = None


def _summary():
    global _SUMMARY
    if _SUMMARY is None:
        _SUMMARY = summarize_dat_file(TESTDATA2)
    return _SUMMARY


def _load_packets():
    packets = []
    with open(TESTDATA2, "rb") as f:
        while True:
            hdr = f.read(4)
            if len(hdr) < 4:
                break
            length = struct.unpack_from("<I", hdr)[0]
            packets.append(f.read(length))
    return packets


class TestFullNightParsing(unittest.TestCase):
    def test_packet_count(self):
        self.assertEqual(_summary().packet_count, 21939)

    def test_duration_approximately_six_hours(self):
        minutes = _summary().duration_seconds / 60.0
        self.assertGreater(minutes, 340)   # at least 5h40m
        self.assertLess(minutes, 400)      # no more than 6h40m

    def test_all_packets_crc_valid(self):
        bad = []
        for i, pkt in enumerate(_load_packets()):
            if not verify_crc(pkt):
                bad.append(i)
        self.assertEqual(bad, [], f"CRC failures at packet indices: {bad[:10]}")

    def test_waveform_channels_present(self):
        ws = _summary().waveform_samples
        for channel in ("OxiA", "OxiB", "PAT", "Chest"):
            self.assertIn(channel, ws, f"{channel} missing from waveform samples")

    def test_waveform_sample_counts_plausible(self):
        ws = _summary().waveform_samples
        for channel, count in ws.items():
            # Each of the 21,939 packets contributes ~100 samples per channel;
            # expect at least 1M and no more than 4M.
            self.assertGreater(count, 1_000_000, f"{channel} sample count too low")
            self.assertLess(count, 4_000_000,    f"{channel} sample count too high")

    def test_body_position_majority_supine(self):
        bp = _summary().body_positions
        self.assertIn("Supine", bp)
        total = sum(bp.values())
        self.assertGreater(bp["Supine"] / total, 0.5, "Expected >50% supine")


class TestFullNightClinicalMetrics(unittest.TestCase):
    def test_ahi_estimate_mild_range(self):
        ahi = _summary().ahi_estimate
        self.assertIsNotNone(ahi)
        # Standard AHI from apnea/central event count over ~6 h
        self.assertGreater(ahi, 0)
        self.assertLess(ahi, 30)

    def test_pahi_estimate_elevated(self):
        pahi = _summary().pahi_estimate
        self.assertIsNotNone(pahi)
        self.assertGreater(pahi, 5)    # any detectable PAT-based AHI

    def test_rdi_estimate_exceeds_pahi(self):
        # pRDI >= pAHI because RERAs are added on top of apneas/hypopneas.
        pahi = _summary().pahi_estimate
        rdi = _summary().rdi_estimate
        self.assertIsNotNone(rdi)
        self.assertGreaterEqual(rdi, pahi)

    def test_apnea_events_nonzero(self):
        self.assertGreater(_summary().apnea_events, 0)

    def test_pat_event_counts_present(self):
        pec = _summary().pat_event_counts
        self.assertGreater(sum(pec.values()), 0)

    def test_hr_mean_plausible(self):
        hr = _summary().hr_mean
        self.assertIsNotNone(hr)
        self.assertFalse(math.isnan(hr))
        self.assertGreater(hr, 40)
        self.assertLess(hr, 120)

    def test_hr_max_above_mean(self):
        self.assertGreater(_summary().hr_max, _summary().hr_mean)

    def test_spo2_mean_plausible(self):
        spo2 = _summary().spo2_mean
        self.assertIsNotNone(spo2)
        self.assertFalse(math.isnan(spo2))
        self.assertGreater(spo2, 60)
        self.assertLess(spo2, 100)

    def test_spo2_min_below_mean(self):
        self.assertLess(_summary().spo2_min, _summary().spo2_mean)

    def test_metric_min_max_ordered(self):
        s = _summary()
        self.assertIsNotNone(s.metric_min)
        self.assertIsNotNone(s.metric_max)
        self.assertLessEqual(s.metric_min, s.metric_max)


class TestFullNightRegressionValues(unittest.TestCase):
    """Pin the exact values produced by the current parser.

    Update these constants when a deliberate change to the parsing or
    derivation logic is made; they are not expected to change otherwise.
    """

    def test_packet_count_exact(self):
        self.assertEqual(_summary().packet_count, 21939)

    def test_ahi_estimate_exact(self):
        self.assertAlmostEqual(_summary().ahi_estimate, 7.876, places=2)

    def test_pahi_estimate_exact(self):
        self.assertAlmostEqual(_summary().pahi_estimate, 49.06, places=1)

    def test_rdi_estimate_exact(self):
        self.assertAlmostEqual(_summary().rdi_estimate, 53.00, places=1)

    def test_apnea_events_exact(self):
        self.assertEqual(_summary().apnea_events, 48)

    def test_central_events_exact(self):
        self.assertEqual(_summary().central_events, 2)

    def test_hr_mean_exact(self):
        self.assertAlmostEqual(_summary().hr_mean, 78.01, places=1)

    def test_spo2_mean_exact(self):
        self.assertAlmostEqual(_summary().spo2_mean, 88.03, places=1)

    def test_spo2_min_exact(self):
        self.assertAlmostEqual(_summary().spo2_min, 67.67, places=1)

    def test_metric_mean_exact(self):
        self.assertAlmostEqual(_summary().metric_mean, -2119.04, places=1)


if __name__ == "__main__":
    unittest.main()
