"""Headless tests for watchpat_gui.py lifecycle and rendering logic."""

import os
import sys
import threading
import time
import tempfile
import unittest
from unittest import mock
from queue import Queue

# Keep matplotlib headless for CI and local test runs.
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "watchpat-mpl"))

import numpy as np
from matplotlib.backend_bases import MouseEvent

# Locate the project root so imports work regardless of cwd.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import watchpat_gui
import watchpat_analysis


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

    def test_smooth_and_downsample_preserves_spo2_left_edge_level(self):
        x = np.arange(30, dtype=float)
        y = np.full(30, 92.0, dtype=float)

        sx, sy = watchpat_gui._smooth_and_downsample(x, y, window=21, max_points=700)

        self.assertEqual(len(sx), 30)
        self.assertEqual(float(sy[0]), 92.0)
        self.assertEqual(float(sy[-1]), 92.0)

    def test_apply_window_icon_is_safe_without_window(self):
        fig = mock.Mock()
        fig.canvas = mock.Mock(manager=mock.Mock(window=None))

        watchpat_gui._apply_window_icon(fig)

    def test_apply_window_icon_uses_ico_for_windows_window_and_default(self):
        window = mock.Mock()
        fig = mock.Mock()
        fig.canvas = mock.Mock(manager=mock.Mock(window=window))

        with mock.patch.object(watchpat_gui.sys, "platform", "win32"):
            with mock.patch.object(watchpat_gui.Path, "exists", return_value=True):
                watchpat_gui._apply_window_icon(fig)

        self.assertEqual(
            window.iconbitmap.call_args_list,
            [
                mock.call(str(watchpat_gui.APP_ICON_ICO)),
                mock.call(default=str(watchpat_gui.APP_ICON_ICO)),
            ],
        )
        window.iconphoto.assert_not_called()
        fig.canvas.draw_idle.assert_called_once_with()

    def test_configure_windows_taskbar_icon_sets_app_id(self):
        shell32 = mock.Mock()
        fake_ctypes = mock.Mock(windll=mock.Mock(shell32=shell32))

        with mock.patch.object(watchpat_gui.sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, {"ctypes": fake_ctypes}):
                watchpat_gui._configure_windows_taskbar_icon()

        shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(
            watchpat_gui.WINDOWS_APP_ID
        )

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

    def test_update_right_aligns_partial_waveforms_in_fixed_window(self):
        self.dashboard.update(frame=0)

        xdata = np.asarray(self.dashboard.wave_lines["OxiA"].get_xdata())
        self.assertEqual(len(xdata), 3)
        self.assertEqual(xdata[0], watchpat_gui.BUFFER_SIZE - 3)
        self.assertEqual(xdata[-1], watchpat_gui.BUFFER_SIZE - 1)
        self.assertEqual(self.dashboard.wave_axes["OxiA"].get_xlim(), (0.0, float(watchpat_gui.BUFFER_SIZE)))

    def test_update_keeps_partial_waveform_ylim_stable_across_small_changes(self):
        self.dashboard.update(frame=0)
        initial_ylim = self.dashboard.wave_axes["OxiA"].get_ylim()

        with self.buffers.lock:
            self.buffers.oxi_a.extend([4307, 4308, 4309])

        self.dashboard.update(frame=1)

        self.assertEqual(self.dashboard.wave_axes["OxiA"].get_ylim(), initial_ylim)

    def test_update_throttles_waveform_ylim_shrink_across_packets(self):
        with self.buffers.lock:
            self.buffers.oxi_a.clear()
            self.buffers.oxi_a.extend([0, 100])
            self.buffers.packet_count = 1

        self.dashboard.update(frame=0)
        initial_ylim = self.dashboard.wave_axes["OxiA"].get_ylim()

        with self.buffers.lock:
            self.buffers.oxi_a.clear()
            self.buffers.oxi_a.extend([40, 60])
            self.buffers.packet_count = 2

        self.dashboard.update(frame=1)

        self.assertEqual(self.dashboard.wave_axes["OxiA"].get_ylim(), initial_ylim)

    def test_update_blends_ahead_samples_before_waveform_buffer_is_full(self):
        controller = mock.Mock()
        controller.buffers = self.buffers
        controller.full_buffers = self.buffers
        controller.current_index = 0
        controller.packet_count = 1
        controller.paused = False
        controller.speed = 1.0
        controller._playhead = 0.5
        controller.advance = mock.Mock(return_value=self.buffers)
        controller.packets = [
            mock.Mock(
                waveforms=[
                    mock.Mock(
                        channel_name="OxiA",
                        samples=list(range(100, 200)),
                    )
                ]
            )
        ]

        self.dashboard.replay_controller = controller
        self.dashboard.update(frame=0)

        ydata = np.asarray(self.dashboard.wave_lines["OxiA"].get_ydata())
        self.assertEqual(len(ydata), 53)
        np.testing.assert_array_equal(ydata[-50:], np.arange(100, 150))

    def test_update_handles_ahead_samples_before_first_packet_is_committed(self):
        empty_buffers = watchpat_gui.SensorBuffers()
        dashboard = watchpat_gui.WatchPATDashboard(empty_buffers)
        self.addCleanup(watchpat_gui.plt.close, dashboard.fig)

        controller = mock.Mock()
        controller.buffers = empty_buffers
        controller.full_buffers = empty_buffers
        controller.current_index = 0
        controller.packet_count = 1
        controller.paused = False
        controller.speed = 1.0
        controller._playhead = 0.25
        controller.advance = mock.Mock(return_value=empty_buffers)
        controller.packets = [
            mock.Mock(
                waveforms=[
                    mock.Mock(
                        channel_name="OxiA",
                        samples=list(range(200, 300)),
                    )
                ]
            )
        ]

        dashboard.replay_controller = controller
        dashboard.update(frame=0)

        ydata = np.asarray(dashboard.wave_lines["OxiA"].get_ydata())
        self.assertEqual(len(ydata), 25)
        np.testing.assert_array_equal(ydata, np.arange(200, 225))

    def test_update_scales_pat_lookahead_to_actual_packet_length(self):
        controller = mock.Mock()
        controller.buffers = self.buffers
        controller.full_buffers = self.buffers
        controller.current_index = 0
        controller.packet_count = 1
        controller.paused = False
        controller.speed = 1.0
        controller._playhead = 0.5
        controller.advance = mock.Mock(return_value=self.buffers)
        controller.packets = [
            mock.Mock(
                waveforms=[
                    mock.Mock(
                        channel_name="PAT",
                        samples=list(range(300, 407)),
                    )
                ]
            )
        ]

        self.dashboard.replay_controller = controller
        self.dashboard.update(frame=0)

        ydata = np.asarray(self.dashboard.wave_lines["PAT"].get_ydata())
        self.assertEqual(len(ydata), 56)
        np.testing.assert_array_equal(ydata[-53:], np.arange(300, 353))

    def test_update_renders_pahi_prdi_counts_and_central_stats(self):
        now = time.time()
        with self.buffers.lock:
            self.buffers.start_time = now - 3600
            self.buffers.spo2_full_times.extend([60.0, 120.0, 180.0])
            self.buffers.spo2_full_history.extend([96.0, 94.0, 95.0])
            self.buffers.pat_amp_times.extend([60.0, 120.0, 180.0])
            self.buffers.pat_amp_history.extend([92.0, 68.0, 88.0])
            self.buffers.pat_events = [
                (60.0, 0.5, 8.0, 4.2, watchpat_gui.EVT_APNEA),
                (120.0, 0.6, 4.0, 3.2, watchpat_gui.EVT_HYPOPNEA),
                (180.0, 0.6, 7.5, 1.0, watchpat_gui.EVT_RERA),
            ]
            self.buffers.central_events = [(240.0, 4.0)]
            self.buffers.pahi_estimate = 2.0
            self.buffers.rdi_estimate = 3.0

        self.dashboard.update(frame=0)

        self.assertEqual(self.dashboard._drawn_evt_count[watchpat_gui.EVT_APNEA], 1)
        self.assertEqual(self.dashboard._drawn_evt_count[watchpat_gui.EVT_HYPOPNEA], 1)
        self.assertEqual(self.dashboard._drawn_evt_count[watchpat_gui.EVT_RERA], 1)
        self.assertEqual(self.dashboard._drawn_evt_count[watchpat_gui.EVT_CENTRAL], 1)
        self.assertEqual(
            self.dashboard.ax_apnea_ahi_text.get_text(),
            "pAHI: 2.0/hr  pRDI: 3.0/hr  (A:1 H:1 R:1 C:1)",
        )
        self.assertEqual(self.dashboard.ax_apnea_ahi_text.get_color(), "#2ecc71")
        stats_text = self.dashboard.stats_text.get_text()
        self.assertIn("pAHI:        2.0 /hr", stats_text)
        self.assertIn("pRDI:        3.0 /hr", stats_text)
        self.assertIn("Central:          1", stats_text)

    def test_update_renders_sleep_stage_overlay_and_percentages(self):
        with self.buffers.lock:
            self.buffers.sleep_stage_times.extend([60.0, 120.0, 180.0, 240.0])
            self.buffers.sleep_stage_history.extend([3, 1, 0, 2])
            self.buffers.sleep_stage_label_history.extend(
                ["Awake", "Light", "Deep", "REM"])
            self.buffers.current_sleep_stage = "REM"

        self.dashboard.update(frame=0)

        self.assertEqual(len(self.dashboard.sleep_stage_line.get_xdata()), 4)
        self.assertEqual(self.dashboard.stage_strip_image.get_array().shape[0], 1)
        stats_text = self.dashboard.stats_text.get_text()
        self.assertIn("Stage:          REM", stats_text)
        self.assertIn("Stages %:  A 25.0 L 25.0 D 25.0 R 25.0", stats_text)
        legend_texts = [text.get_text() for text in self.dashboard.ax_apnea.texts]
        self.assertIn("SpO2 line", legend_texts)
        self.assertIn("Gray bars = respiratory events / 5 min", legend_texts)
        self.assertIn("Stages:", legend_texts)
        strip = np.asarray(self.dashboard.stage_strip_image.get_array())
        np.testing.assert_array_equal(strip[0, 0], watchpat_gui.SLEEP_STAGE_BG_RGBA)

    def test_update_keeps_empty_stage_strip_background_black(self):
        with self.buffers.lock:
            self.buffers.sleep_stage_times.clear()
            self.buffers.sleep_stage_history.clear()
            self.buffers.sleep_stage_label_history.clear()

        self.dashboard.update(frame=0)

        np.testing.assert_array_equal(
            self.dashboard.stage_strip_image.get_array(),
            watchpat_gui.SLEEP_STAGE_BG_RGBA.reshape(1, 1, 4),
        )

    def test_update_ignores_invalid_startup_spo2_zero(self):
        with self.buffers.lock:
            self.buffers.spo2_history.clear()
            self.buffers.spo2_history.extend([0.0, 92.0, 93.0])
            self.buffers.spo2_full_times.clear()
            self.buffers.spo2_full_history.clear()
            self.buffers.spo2_full_times.extend([60.0, 120.0, 180.0])
            self.buffers.spo2_full_history.extend([0.0, 92.0, 93.0])

        self.dashboard.update(frame=0)

        trend_y = np.asarray(self.dashboard.spo2_trend_line.get_ydata())
        full_y = np.asarray(self.dashboard.spo2_full_line.get_ydata())
        self.assertTrue(np.all(trend_y > 0))
        self.assertTrue(np.all(full_y > 0))
        self.assertEqual(float(full_y[0]), 92.0)

    def test_update_smooths_long_apnea_panel_traces(self):
        with self.buffers.lock:
            self.buffers.start_time = time.time() - 3600
            for idx in range(1500):
                self.buffers.spo2_full_times.append(float(idx))
                self.buffers.spo2_full_history.append(90.0 + (idx % 7))
                self.buffers.pat_events.append(
                    (float(idx), 0.5, 5.0, 3.5, watchpat_gui.EVT_HYPOPNEA))

        self.dashboard.update(frame=0)

        self.assertLess(len(self.dashboard.spo2_full_line.get_xdata()), 1000)
        visible_bars = sum(
            1 for patch in self.dashboard.event_bar_patches if patch.get_visible()
        )
        self.assertGreater(visible_bars, 0)

    def test_update_ahi_color_thresholds_follow_pahi_severity(self):
        cases = [
            (4.9, "#2ecc71"),
            (5.0, "#f39c12"),
            (15.0, "#e74c3c"),
        ]

        for pahi, expected_color in cases:
            with self.subTest(pahi=pahi):
                with self.buffers.lock:
                    self.buffers.pahi_estimate = pahi
                    self.buffers.rdi_estimate = pahi + 1.0
                self.dashboard.update(frame=0)
                self.assertEqual(
                    self.dashboard.ax_apnea_ahi_text.get_color(),
                    expected_color,
                )

    def test_replay_scrubber_seek_updates_dashboard_buffer(self):
        controller = mock.Mock()
        controller.buffers = watchpat_gui.SensorBuffers()
        controller.full_buffers = watchpat_gui.SensorBuffers()
        controller.current_index = 3
        controller.packet_count = 10
        controller.paused = True
        controller.speed = 1.0
        controller._playhead = 3.0
        controller.seek = mock.Mock(return_value=controller.buffers)
        controller.advance = mock.Mock(return_value=controller.buffers)

        self.dashboard.enable_replay_scrubber(controller)
        event = MouseEvent(
            "button_press_event",
            self.dashboard.fig.canvas,
            x=0,
            y=0,
            button=1,
        )
        event.inaxes = self.dashboard.ax_apnea
        event.xdata = 5.0 / 60.0
        self.dashboard.fig.canvas.callbacks.process("button_press_event", event)

        controller.seek.assert_called_with(5)
        self.assertIs(self.dashboard.buffers, controller.buffers)

    def test_replay_scrubber_seek_resets_waveform_ylim_cache(self):
        controller = mock.Mock()
        controller.buffers = watchpat_gui.SensorBuffers()
        controller.full_buffers = watchpat_gui.SensorBuffers()
        controller.current_index = 3
        controller.packet_count = 10
        controller.paused = True
        controller.speed = 1.0
        controller._playhead = 3.0
        controller.seek = mock.Mock(return_value=controller.buffers)
        controller.advance = mock.Mock(return_value=controller.buffers)

        self.dashboard._wave_y_limits["OxiA"] = (0.0, 1.0)
        self.dashboard._wave_y_history["OxiA"] = watchpat_gui.deque([(0.0, 1.0)])
        self.dashboard._wave_y_last_packet["OxiA"] = 99
        self.dashboard._wave_y_last_update_packet["OxiA"] = 99

        self.dashboard.enable_replay_scrubber(controller)
        event = MouseEvent(
            "button_press_event",
            self.dashboard.fig.canvas,
            x=0,
            y=0,
            button=1,
        )
        event.inaxes = self.dashboard.ax_apnea
        event.xdata = 5.0 / 60.0
        self.dashboard.fig.canvas.callbacks.process("button_press_event", event)

        self.assertEqual(self.dashboard._wave_y_limits, {})
        self.assertEqual(self.dashboard._wave_y_history, {})
        self.assertEqual(self.dashboard._wave_y_last_packet, {})
        self.assertEqual(self.dashboard._wave_y_last_update_packet, {})

    def test_replay_update_advances_controller_and_syncs_slider(self):
        controller = mock.Mock()
        controller.buffers = self.buffers
        controller.full_buffers = self.buffers
        controller.current_index = 4
        controller.packet_count = 12
        controller.paused = False
        controller.speed = 2.0
        controller._playhead = 4.0
        controller.advance = mock.Mock(return_value=self.buffers)

        self.dashboard.enable_replay_scrubber(controller)
        artists = self.dashboard.update(frame=0)

        controller.advance.assert_called_once()
        self.assertGreater(len(artists), 0)
        self.assertIn("Elapsed:      0m 04s", self.dashboard.stats_text.get_text())
        self.assertIsNotNone(self.dashboard.replay_status_text)
        self.assertIn("4/12", self.dashboard.replay_status_text.get_text())
        self.assertFalse(self.dashboard._replay_button_is_paused)

    def test_update_attaches_loaded_replay_controller(self):
        controller = mock.Mock()
        controller.buffers = self.buffers
        controller.full_buffers = self.buffers
        controller.current_index = 0
        controller.packet_count = 12
        controller.paused = True
        controller.speed = 1.0
        controller._playhead = 0.0
        controller.advance = mock.Mock(return_value=self.buffers)

        replay_queue = Queue()
        replay_queue.put(("ready", controller))
        self.dashboard.attach_replay_loader(replay_queue, "Replay: ready")

        self.dashboard.update(frame=0)

        self.assertIs(self.dashboard.replay_controller, controller)
        self.assertEqual(self.dashboard.mode_text.get_text(), "Replay: ready")
        self.assertIsNotNone(self.dashboard.replay_button)
        self.assertIsNotNone(self.dashboard.replay_status_text)

    def test_replay_button_uses_play_pause_icons(self):
        controller = mock.Mock()
        controller.buffers = watchpat_gui.SensorBuffers()
        controller.full_buffers = watchpat_gui.SensorBuffers()
        controller.current_index = 0
        controller.packet_count = 10
        controller.paused = True
        controller.speed = 1.0
        controller.seek = mock.Mock(return_value=controller.buffers)
        controller.advance = mock.Mock(return_value=controller.buffers)

        self.dashboard.enable_replay_scrubber(controller)
        self.assertTrue(self.dashboard._replay_button_is_paused)

    def test_event_markers_reset_when_scrubbing_backwards(self):
        with self.buffers.lock:
            self.buffers.start_time = time.time() - 3600
            self.buffers.spo2_full_times.extend([60.0])
            self.buffers.spo2_full_history.extend([95.0])
            self.buffers.pat_events = [
                (60.0, 0.5, 8.0, 4.2, watchpat_gui.EVT_APNEA),
                (120.0, 0.6, 4.0, 3.2, watchpat_gui.EVT_HYPOPNEA),
            ]
        self.dashboard.update(frame=0)
        self.assertEqual(self.dashboard._drawn_evt_count[watchpat_gui.EVT_APNEA], 1)
        self.assertEqual(self.dashboard._drawn_evt_count[watchpat_gui.EVT_HYPOPNEA], 1)

        with self.buffers.lock:
            self.buffers.pat_events = [
                (60.0, 0.5, 8.0, 4.2, watchpat_gui.EVT_APNEA),
            ]
        self.dashboard.update(frame=0)
        self.assertEqual(self.dashboard._drawn_evt_count[watchpat_gui.EVT_APNEA], 1)
        self.assertEqual(self.dashboard._drawn_evt_count[watchpat_gui.EVT_HYPOPNEA], 0)

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


class TestReplayController(unittest.TestCase):
    def test_replay_full_buffers_keep_entire_session_history(self):
        packet = mock.Mock(raw_payload=b"a", waveforms=[], motion=None,
                           metric=None, events=[])

        with mock.patch.object(watchpat_gui, "read_dat_file", return_value=[b"a"]):
            with mock.patch.object(watchpat_gui, "parse_data_packet",
                                   return_value=packet):
                ctrl = watchpat_gui.ReplayController(
                    "capture.dat", speed=1.0, use_cache=False)

        self.assertIsNone(ctrl.full_buffers.spo2_full_history.maxlen)
        self.assertIsNone(ctrl.full_buffers.sleep_stage_times.maxlen)
        self.assertEqual(ctrl.checkpoints[0].spo2_full_history.maxlen, 4 * 3600)

    def test_nocache_skips_cache_path(self):
        packet_a = mock.Mock(raw_payload=b"a", waveforms=[], motion=None,
                             metric=None, events=[])

        with mock.patch.object(watchpat_gui, "read_dat_file", return_value=[b"a"]):
            with mock.patch.object(watchpat_gui, "parse_data_packet",
                                   return_value=packet_a):
                ctrl = watchpat_gui.ReplayController(
                    "capture.dat", speed=1.0, use_cache=False)

        self.assertFalse(ctrl.use_cache)
        self.assertEqual(ctrl.packet_count, 1)

    def test_seek_rebuilds_buffers_to_requested_packet(self):
        packet_a = mock.Mock(raw_payload=b"a", waveforms=[], motion=None,
                             metric=None, events=[])
        packet_b = mock.Mock(raw_payload=b"bb", waveforms=[], motion=None,
                             metric=None, events=[])

        with mock.patch.object(watchpat_gui, "read_dat_file", return_value=[b"a", b"b"]):
            with mock.patch.object(watchpat_gui, "parse_data_packet",
                                   side_effect=[packet_a, packet_b]):
                ctrl = watchpat_gui.ReplayController(
                    "capture.dat", speed=1.0, use_cache=False)

        ctrl.seek(1)
        self.assertEqual(ctrl.current_index, 1)
        self.assertEqual(ctrl.buffers.packet_count, 1)
        self.assertEqual(ctrl.buffers.total_bytes, 1)

        ctrl.seek(2)
        self.assertEqual(ctrl.current_index, 2)
        self.assertEqual(ctrl.buffers.packet_count, 2)
        self.assertEqual(ctrl.buffers.total_bytes, 3)

    def test_advance_feeds_packets_until_target_index(self):
        packet_a = mock.Mock(raw_payload=b"a", waveforms=[], motion=None,
                             metric=None, events=[])
        packet_b = mock.Mock(raw_payload=b"bb", waveforms=[], motion=None,
                             metric=None, events=[])

        with mock.patch.object(watchpat_gui, "read_dat_file", return_value=[b"a", b"b"]):
            with mock.patch.object(watchpat_gui, "parse_data_packet",
                                   side_effect=[packet_a, packet_b]):
                ctrl = watchpat_gui.ReplayController(
                    "capture.dat", speed=2.0, use_cache=False)

        ctrl.advance(2.0)
        self.assertEqual(ctrl.current_index, 2)
        self.assertEqual(ctrl.buffers.packet_count, 2)
        self.assertTrue(ctrl.paused)


class TestApneaClassification(unittest.TestCase):
    """Tests for PAT event classification, AHI/RDI counting, and central detection.

    Strategy: directly manipulate SensorBuffers internal state to set up each
    scenario, then call _update_derived() to run the classification logic.

    PAT envelope is synthesized as a 1.2 Hz sinusoid (≈72 BPM) with amplitude
    1000.  With a pre-seeded baseline of 1000, the recovered ratio is ≈2.0 which
    is well above the 0.80 recovery threshold, triggering event termination and
    classification on every call.
    """

    RATE = watchpat_gui.WAVEFORM_RATE  # 100 Hz

    def _sinusoid(self, amplitude=1000, n_samples=500, freq_hz=1.2):
        t = np.arange(n_samples)
        return (amplitude * np.sin(2 * np.pi * freq_hz * t / self.RATE)).astype(int).tolist()

    def _recovering_buffers(self, spo2_history, hr_baseline,
                             event_duration_s=15.0):
        """Return SensorBuffers with a PAT event that is currently recovering.

        The PAT buffer contains a high-amplitude sinusoid (ratio ≈2.0 vs
        pre-seeded baseline of 1000), so _update_derived will detect recovery
        and classify the event.  spo2_full_history is pre-populated with the
        supplied values so the spo2_drop measurement uses those directly.
        """
        buf = watchpat_gui.SensorBuffers()
        now = time.time()
        with buf.lock:
            buf.start_time = now - 3600
            elapsed = time.time() - buf.start_time  # ≈3600 s
            buf.pat.extend(self._sinusoid(amplitude=1000))
            buf._pat_baseline_buf.extend([1000.0] * 100)
            buf._pat_in_event = True
            buf._pat_event_start = elapsed - event_duration_s
            buf._pat_hr_baseline = float(hr_baseline)
            buf.spo2_full_history.extend(spo2_history)
        return buf

    # ------------------------------------------------------------------
    # Snapshot field coverage
    # ------------------------------------------------------------------

    def test_snapshot_includes_new_fields(self):
        buf = watchpat_gui.SensorBuffers()
        snap = buf.snapshot()
        for key in ("pat_amp_history", "pat_amp_times", "pat_events",
                    "pahi_estimate", "rdi_estimate", "central_events",
                    "sleep_stage_history", "sleep_stage_times",
                    "sleep_stage_percentages", "current_sleep_stage"):
            self.assertIn(key, snap, msg=f"snapshot missing: {key}")

    def test_sleep_stage_percentages_are_reported(self):
        buf = watchpat_gui.SensorBuffers()
        with buf.lock:
            buf.sleep_stage_label_history.extend(
                ["Awake", "Awake", "Light", "Deep", "REM"])
        snap = buf.snapshot()
        self.assertEqual(snap["sleep_stage_percentages"]["Awake"], 40.0)
        self.assertEqual(snap["sleep_stage_percentages"]["Light"], 20.0)
        self.assertEqual(snap["sleep_stage_percentages"]["Deep"], 20.0)
        self.assertEqual(snap["sleep_stage_percentages"]["REM"], 20.0)

    # ------------------------------------------------------------------
    # PAT event type classification
    # ------------------------------------------------------------------

    def test_pat_event_classified_as_apnea(self):
        """PAT attenuation + SpO₂ drop ≥4% → APNEA."""
        buf = self._recovering_buffers([96.0] * 10 + [91.0] * 10,
                                       hr_baseline=60.0)
        with buf.lock:
            buf._update_derived()
        self.assertEqual(len(buf.pat_events), 1)
        self.assertEqual(buf.pat_events[0][4], watchpat_gui.EVT_APNEA)

    def test_pat_event_classified_as_hypopnea(self):
        """PAT attenuation + SpO₂ drop 3–4% → HYPOPNEA."""
        buf = self._recovering_buffers([96.0] * 10 + [92.5] * 10,
                                       hr_baseline=60.0)
        with buf.lock:
            buf._update_derived()
        self.assertEqual(len(buf.pat_events), 1)
        self.assertEqual(buf.pat_events[0][4], watchpat_gui.EVT_HYPOPNEA)

    def test_pat_event_classified_as_rera(self):
        """PAT attenuation + HR rise ≥6 BPM, SpO₂ drop <3% → RERA.

        The sinusoid produces HR ≈72 BPM; with hr_baseline=60 that gives
        hr_rise ≈12, reliably above the 6 BPM threshold.
        """
        buf = self._recovering_buffers([96.0] * 20, hr_baseline=60.0)
        with mock.patch.object(watchpat_analysis, "_compute_heart_rate", return_value=72.0):
            with buf.lock:
                buf._update_derived()
        self.assertEqual(len(buf.pat_events), 1)
        self.assertEqual(buf.pat_events[0][4], watchpat_gui.EVT_RERA)

    def test_pat_event_classified_as_pat_when_no_markers(self):
        """PAT attenuation, no SpO₂ drop, no valid HR baseline → PAT (unclassified)."""
        # hr_baseline=-1 forces hr_rise=0.0 via the guard in _update_derived
        buf = self._recovering_buffers([96.0] * 20, hr_baseline=-1.0)
        with buf.lock:
            buf._update_derived()
        self.assertEqual(len(buf.pat_events), 1)
        self.assertEqual(buf.pat_events[0][4], watchpat_gui.EVT_PAT)

    # ------------------------------------------------------------------
    # AHI / RDI counting
    # ------------------------------------------------------------------

    def test_pahi_counts_apnea_and_hypopnea_only(self):
        """pAHI = (APNEA+HYPOPNEA)/hr; pRDI adds RERA; EVT_PAT excluded."""
        buf = watchpat_gui.SensorBuffers()
        now = time.time()
        with buf.lock:
            buf.start_time = now - 3600  # 1-hour session
            buf.pat_events = [
                (100.0, 0.5, 5.0, 5.0, watchpat_gui.EVT_APNEA),
                (200.0, 0.5, 5.0, 5.0, watchpat_gui.EVT_APNEA),
                (300.0, 0.5, 5.0, 5.0, watchpat_gui.EVT_APNEA),
                (400.0, 0.5, 5.0, 3.5, watchpat_gui.EVT_HYPOPNEA),
                (500.0, 0.5, 5.0, 3.5, watchpat_gui.EVT_HYPOPNEA),
                (600.0, 0.5, 9.0, 1.0, watchpat_gui.EVT_RERA),
                (700.0, 0.5, 9.0, 1.0, watchpat_gui.EVT_RERA),
                (800.0, 0.5, 1.0, 0.5, watchpat_gui.EVT_PAT),
            ]
            buf._update_derived()
        # 3 APNEA + 2 HYPOPNEA = 5 events over 1 hour
        self.assertAlmostEqual(buf.pahi_estimate, 5.0, places=1)
        # + 2 RERA = 7 events
        self.assertAlmostEqual(buf.rdi_estimate, 7.0, places=1)

    def test_pat_event_shorter_than_ten_seconds_is_not_counted(self):
        """PAT attenuations under 10 s should terminate without creating an event."""
        buf = self._recovering_buffers([96.0] * 20, hr_baseline=60.0,
                                       event_duration_s=9.0)
        with buf.lock:
            buf._update_derived()
        self.assertEqual(buf.pat_events, [])
        self.assertFalse(buf._pat_in_event)

    # ------------------------------------------------------------------
    # Central apnea detection
    # ------------------------------------------------------------------

    def _central_buffers(self, has_concurrent_pat):
        """Buffers set up with an ODI3 event recovering at session time 100 s.

        Seeding _spo2_raw and _spo2_ema makes _update_derived produce a
        positive current_spo2 (≈95.5) even with empty oxi channels, so the
        ODI3 detection block runs.  The baseline is 96.0, giving drop ≈0.5
        which is <1.0 and triggers recovery.
        """
        buf = watchpat_gui.SensorBuffers()
        now = time.time()
        with buf.lock:
            buf.start_time = now - 3600
            buf._spo2_raw.extend([95.5] * 14)
            buf._spo2_ema = 95.5
            buf._desat_in_event = True
            buf._desat_start_elapsed = 100.0
            buf._desat_nadir = 91.0
            buf._desat_baseline.extend([96.0] * 50)
            if has_concurrent_pat:
                buf.pat_events = [
                    (100.0, 0.5, 8.0, 4.5, watchpat_gui.EVT_APNEA)]
        return buf

    def test_central_detected_without_concurrent_pat(self):
        """SpO₂ drop with no nearby PAT event → added to central_events."""
        buf = self._central_buffers(has_concurrent_pat=False)
        with buf.lock:
            buf._update_derived()
        self.assertEqual(len(buf.central_events), 1)
        self.assertAlmostEqual(buf.central_events[0][0], 100.0, places=0)

    def test_central_not_detected_with_concurrent_pat(self):
        """SpO₂ drop coinciding with a PAT event → NOT classified as central."""
        buf = self._central_buffers(has_concurrent_pat=True)
        with buf.lock:
            buf._update_derived()
        self.assertEqual(len(buf.central_events), 0)

    def test_central_not_detected_during_active_pat_event(self):
        """An active PAT event suppresses central classification immediately."""
        buf = self._central_buffers(has_concurrent_pat=False)
        with buf.lock:
            buf._pat_in_event = True
            buf._update_derived()
        self.assertEqual(len(buf.central_events), 0)


if __name__ == "__main__":
    unittest.main()
