"""Unit tests for watchpat_to_resmed_sd.py helpers."""

import os
import sys
import tempfile
import unittest
from array import array
from datetime import datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from watchpat_to_resmed_sd import (
    Segment,
    SignalSamples,
    build_ahi_waveform_signals,
    slice_signal_values,
    write_ahi_aux_edf,
)


class TestResmedAhiExport(unittest.TestCase):
    def test_slice_signal_values_pads_to_full_second(self):
        signal = SignalSamples("PAT", array("h", [10, 11, 12, 13, 14]), 2)

        values = slice_signal_values(signal, offset_seconds=1, duration_seconds=2)

        self.assertEqual(values, [12, 13, 14, 0])

    def test_build_ahi_waveform_signals_includes_required_channels(self):
        channels = {
            "OxiA": SignalSamples("OxiA", array("h", [1, 2, 3, 4]), 2),
            "OxiB": SignalSamples("OxiB", array("h", [5, 6, 7, 8]), 2),
            "PAT": SignalSamples("PAT", array("h", [9, 10, 11, 12]), 2),
            "Chest": SignalSamples("Chest", array("h", [13, 14, 15, 16]), 2),
        }

        signals = build_ahi_waveform_signals(channels, offset_seconds=0, duration_seconds=2)

        self.assertEqual([signal.label for signal in signals],
                         ["OxiA.raw", "OxiB.raw", "PAT.raw", "Chest.raw"])
        self.assertTrue(all(signal.samples_per_record == 2 for signal in signals))
        self.assertTrue(all(len(signal.values) == 4 for signal in signals))

    def test_write_ahi_aux_edf_creates_sidecar_files(self):
        channels = {
            "OxiA": SignalSamples("OxiA", array("h", [1, 2, 3, 4]), 2),
            "OxiB": SignalSamples("OxiB", array("h", [5, 6, 7, 8]), 2),
            "PAT": SignalSamples("PAT", array("h", [9, 10, 11, 12]), 2),
            "Chest": SignalSamples("Chest", array("h", [13, 14, 15, 16]), 2),
        }
        segment = Segment(
            start_dt=datetime(2025, 1, 2, 3, 4, 5),
            pulse_values=[60, 61],
            spo2_values=[95, 96],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            write_ahi_aux_edf(out_dir, "12345", segment.start_dt, channels, segment)

            edf_path = out_dir / "20250102_030405_AHI.edf"
            crc_path = out_dir / "20250102_030405_AHI.crc"
            self.assertTrue(edf_path.is_file())
            self.assertTrue(crc_path.is_file())
            self.assertGreater(edf_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
