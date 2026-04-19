"""Headless tests for watchpat_gui.py lifecycle and rendering logic."""

import os
import sys
import threading
import time
import tempfile
import unittest
from unittest import mock

import numpy as np

# Keep matplotlib headless for CI and local test runs.
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "watchpat-mpl"))

# Locate the project root so imports work regardless of cwd.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import watchpat_gui


class TestWatchPATDashboard(unittest.TestCase):
    def setUp(self):
        self.buffers = watchpat_gui.SensorBuffers()
        now = time.time()
        with self.buffers.lock:
            self.buffers.oxi_a.extend([4434, 4306, 4178])
            self.buffers.oxi_b.extend([14196, 14196, 14260])
            self.buffers.pat.extend([22269, 22269, 22333])
            self.buffers.chest.extend([1603, 1602, 1609])
            self.buffers.accel_x.extend([-80, -75, -77])
            self.buffers.accel_y.extend([1092, 1085, 1080])
            self.buffers.accel_z.extend([219, 219, 225])
            self.buffers.field_a.extend([27, 31, 23])
            self.buffers.field_b.extend([22, 22, 30])
            self.buffers.hr_history.extend([60.0, 61.0, 62.0])
            self.buffers.spo2_history.extend([95.0, 96.0, np.nan])
            self.buffers.current_hr = 62.4
            self.buffers.current_spo2 = 95.2
            self.buffers.body_position = "y+"
            self.buffers.body_position_label = "Left"
            self.buffers.metric_value = 123
            self.buffers.packet_count = 7
            self.buffers.total_bytes = 2048
            self.buffers.start_time = now - 65
            self.buffers.last_motion_crc_ok = 5
            self.buffers.last_motion_crc_total = 5
            self.buffers.events.append("#7: EVENT val=16")

        self.dashboard = watchpat_gui.WatchPATDashboard(self.buffers)
        self.addCleanup(watchpat_gui.plt.close, self.dashboard.fig)

    def test_update_populates_dashboard_text_and_lines(self):
        artists = self.dashboard.update(frame=0)

        self.assertEqual(self.dashboard.hr_value_text.get_text(), "62")
        self.assertEqual(self.dashboard.spo2_value_text.get_text(), "95")
        self.assertEqual(self.dashboard.pos_text.get_text(), "Left")
        self.assertEqual(self.dashboard.pos_label.get_text(), "(y+)")
        self.assertIn("Packets:", self.dashboard.stats_text.get_text())
        self.assertIn("CRC:", self.dashboard.stats_text.get_text())
        self.assertEqual(len(self.dashboard.wave_lines["OxiA"].get_xdata()), 3)
        self.assertEqual(len(self.dashboard.accel_lines["x"].get_ydata()), 3)
        self.assertGreater(len(artists), 0)

    def test_request_close_stops_animation_once(self):
        stop = mock.Mock()
        self.dashboard.anim = mock.Mock(event_source=mock.Mock(stop=stop))

        with mock.patch.object(watchpat_gui.plt, "close") as close_mock:
            self.dashboard.request_close()
            self.dashboard.request_close()

        self.assertTrue(self.dashboard._closing)
        stop.assert_called_once_with()
        close_mock.assert_called_once_with(self.dashboard.fig)

    def test_run_stops_animation_after_show_returns(self):
        fake_anim = mock.Mock(event_source=mock.Mock(stop=mock.Mock()))

        with mock.patch.object(watchpat_gui, "FuncAnimation", return_value=fake_anim) as anim_mock:
            with mock.patch.object(watchpat_gui.plt, "show") as show_mock:
                self.dashboard.run()

        anim_mock.assert_called_once()
        show_mock.assert_called_once_with()
        fake_anim.event_source.stop.assert_called_once_with()


class TestBleFeeder(unittest.TestCase):
    def test_ble_feeder_respects_pre_set_stop_event(self):
        stop_event = threading.Event()
        stop_event.set()
        client_instances = []

        class FakeClient:
            def __init__(self):
                self.connect = mock.AsyncMock()
                client_instances.append(self)

            async def scan(self, timeout, serial_filter, stop_event):
                self.scan_args = (timeout, serial_filter, stop_event)
                return ["device"]

        with mock.patch.object(watchpat_gui, "WatchPATClient", FakeClient):
            watchpat_gui.ble_feeder("SER123", watchpat_gui.SensorBuffers(), 0.5, stop_event, "")

        self.assertEqual(len(client_instances), 1)
        fake = client_instances[0]
        self.assertEqual(fake.scan_args[0], 0.5)
        self.assertEqual(fake.scan_args[1], "SER123")
        self.assertIs(fake.scan_args[2], stop_event)
        fake.connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
