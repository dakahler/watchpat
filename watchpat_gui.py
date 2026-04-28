"""
WatchPAT ONE Real-Time Dashboard
=================================
GUI for viewing live or replayed WatchPAT sensor data with rolling
waveform plots and status indicators.

Usage:
    python watchpat_gui.py --live                         Live BLE (auto-detect)
    python watchpat_gui.py --serial XXXXXXXXX             Live BLE (specific device)
    python watchpat_gui.py --replay capture.dat           Replay a capture file
    python watchpat_gui.py --replay capture.dat --speed 5 Replay at 5x speed
"""

import argparse
import asyncio
from concurrent.futures import ProcessPoolExecutor
import logging
import math
import os
import pickle
import struct
import sys
import threading
import time
import tempfile
from collections import deque
from hashlib import sha1
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

import matplotlib


def _running_unittest() -> bool:
    argv = sys.argv
    return (
        len(argv) >= 3
        and argv[1] == "-m"
        and argv[2] == "unittest"
    )


def _configure_matplotlib_backend():
    """Prefer an interactive backend that can also accept a custom app icon."""
    backend_logger = logging.getLogger("watchpat.gui")
    if os.environ.get("MPLBACKEND"):
        return
    if _running_unittest():
        os.environ.setdefault(
            "MPLCONFIGDIR",
            os.path.join(tempfile.gettempdir(), "watchpat-mpl"),
        )
        matplotlib.use("Agg")
        backend_logger.info("Using matplotlib backend: Agg (unittest)")
        return
    preferred = ["MacOSX", "TkAgg"] if sys.platform == "darwin" else ["TkAgg"]
    for backend in preferred:
        try:
            matplotlib.use(backend)
            backend_logger.info("Using matplotlib backend: %s", backend)
            return
        except Exception:
            backend_logger.debug(
                "Matplotlib backend %s unavailable", backend, exc_info=True
            )


_configure_matplotlib_backend()
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import FancyBboxPatch, Circle, Polygon, Rectangle
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
from matplotlib.widgets import Button
import numpy as np

from watchpat_ble import (
    HEADER_SIZE,
    WatchPATClient, ParsedDataPacket, DecodedWaveform,
    MotionRecord, MetricRecord, RecordKind,
    read_dat_file, parse_data_packet, format_parsed_packet,
)

logger = logging.getLogger("watchpat.gui")

from watchpat_analysis import (
    WINDOW_SECONDS, WAVEFORM_RATE, MOTION_RATE, BUFFER_SIZE, MOTION_BUFFER,
    DERIVED_HISTORY, DERIVE_INTERVAL, POSITION_LABELS,
    EVT_APNEA, EVT_HYPOPNEA, EVT_RERA, EVT_PAT, EVT_CENTRAL,
    SLEEP_STAGE_AWAKE, SLEEP_STAGE_LIGHT, SLEEP_STAGE_DEEP, SLEEP_STAGE_REM,
    SLEEP_STAGE_LEVELS,
    SensorBuffers,
)

FPS = 15
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
WAVEFORM_Y_SHRINK_ALPHA = 0.05
WAVEFORM_Y_ENVELOPE_PACKETS = 6
WAVEFORM_Y_UPDATE_EVERY_PACKETS = 3
REPLAY_CACHE_VERSION = 2
APP_ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"
APP_ICON_PNG = APP_ICON_DIR / "watchpat_app_icon.png"
APP_ICON_ICO = APP_ICON_DIR / "watchpat_app_icon.ico"
WINDOWS_APP_ID = "WatchPAT.ONE.Dashboard"

CHANNEL_COLORS = {
    "OxiA":  "#e74c3c",
    "OxiB":  "#3498db",
    "PAT":   "#2ecc71",
    "Chest": "#f39c12",
}

EVENT_COLORS = {
    EVT_APNEA:    "#e74c3c",
    EVT_HYPOPNEA: "#e67e22",
    EVT_RERA:     "#f1c40f",
    EVT_PAT:      "#95a5a6",
    EVT_CENTRAL:  "#9b59b6",
}

SLEEP_STAGE_COLORS = {
    SLEEP_STAGE_AWAKE: "#ffd84d",
    SLEEP_STAGE_LIGHT: "#5cc8ff",
    SLEEP_STAGE_DEEP: "#2ee6a6",
    SLEEP_STAGE_REM: "#ff73c6",
}
SLEEP_STAGE_BG_RGBA = np.array([0.0, 0.0, 0.0, 1.0], dtype=float)


def _smooth_and_downsample(x, y, window: int = 9, max_points: int = 600):
    if len(x) == 0 or len(y) == 0:
        return np.array([]), np.array([])
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    valid = ~np.isnan(y_arr)
    x_arr = x_arr[valid]
    y_arr = y_arr[valid]
    if len(x_arr) == 0:
        return np.array([]), np.array([])
    if window > 1 and len(y_arr) >= window:
        kernel = np.ones(window, dtype=float) / float(window)
        pad_l = (window - 1) // 2
        pad_r = window - 1 - pad_l
        padded = np.pad(y_arr, (pad_l, pad_r), mode="edge")
        y_arr = np.convolve(padded, kernel, mode="valid")
    if len(x_arr) > max_points:
        step = int(math.ceil(len(x_arr) / float(max_points)))
        x_arr = x_arr[::step]
        y_arr = y_arr[::step]
    return x_arr, y_arr


def _valid_spo2_xy(x, y):
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    valid = np.isfinite(y_arr) & (y_arr > 0)
    return x_arr[valid], y_arr[valid]


def _apply_window_icon(fig):
    """Best-effort desktop icon for Tk-backed windows."""
    manager = getattr(fig.canvas, "manager", None)
    window = getattr(manager, "window", None)
    if window is None:
        return
    try:
        icon_set = False
        if sys.platform.startswith("win") and APP_ICON_ICO.exists():
            iconbitmap = getattr(window, "iconbitmap", None)
            if callable(iconbitmap):
                iconbitmap(str(APP_ICON_ICO))
                iconbitmap(default=str(APP_ICON_ICO))
                icon_set = True
        elif APP_ICON_PNG.exists():
            import tkinter as tk

            iconphoto = getattr(window, "iconphoto", None)
            if callable(iconphoto):
                image = tk.PhotoImage(file=str(APP_ICON_PNG))
                # Keep a reference alive for Tk.
                setattr(window, "_watchpat_icon_image", image)
                iconphoto(True, image)
                icon_set = True
        if icon_set:
            fig.canvas.draw_idle()
    except Exception:
        logger.debug("Unable to apply desktop window icon.", exc_info=True)


def _configure_windows_taskbar_icon():
    """Give Windows a stable app identity before the GUI window is created."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_ID
        )
    except Exception:
        logger.debug("Unable to configure Windows taskbar icon.", exc_info=True)


def _normalize_replay_payload(raw: bytes) -> bytes:
    """Accept either a payload-only capture record or a full BLE packet."""
    if len(raw) >= HEADER_SIZE and raw[:2] == b"\xBB\xBB":
        return raw[HEADER_SIZE:]
    return raw


def _decode_packet_chunk(chunk):
    start_idx, raw_packets = chunk
    return [
        parse_data_packet(_normalize_replay_payload(raw), start_idx + offset)
        for offset, raw in enumerate(raw_packets)
    ]


def _decode_packets_for_replay(path: str) -> list[ParsedDataPacket]:
    raw_packets = list(read_dat_file(path))
    if len(raw_packets) <= 512 or os.cpu_count() == 1:
        return [
            parse_data_packet(_normalize_replay_payload(raw), idx)
            for idx, raw in enumerate(raw_packets)
        ]

    # Replay loading runs in a background thread so the GUI stays responsive.
    # On macOS, spawning a process pool from that worker thread has proven
    # unreliable; prefer the straightforward in-process decode in that case.
    if threading.current_thread() is not threading.main_thread():
        return [
            parse_data_packet(_normalize_replay_payload(raw), idx)
            for idx, raw in enumerate(raw_packets)
        ]

    workers = min(8, max(2, os.cpu_count() or 2))
    chunk_size = max(256, int(math.ceil(len(raw_packets) / float(workers * 4))))
    chunks = [
        (start_idx, raw_packets[start_idx:start_idx + chunk_size])
        for start_idx in range(0, len(raw_packets), chunk_size)
    ]
    packets: list[ParsedDataPacket] = []
    try:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for decoded in executor.map(_decode_packet_chunk, chunks):
                packets.extend(decoded)
    except Exception:
        logger.warning(
            "Replay multiprocessing unavailable; falling back to serial decode.",
            exc_info=True,
        )
        return [
            parse_data_packet(_normalize_replay_payload(raw), idx)
            for idx, raw in enumerate(raw_packets)
        ]
    return packets


class ReplayController:
    """Owns replay state so the GUI can seek and scrub arbitrarily."""

    CHECKPOINT_INTERVAL = 300

    def __init__(self, path: str, speed: float = 1.0, use_cache: bool = True):
        self.path = path
        self.speed = speed
        self.use_cache = use_cache
        self._playhead = 0.0
        self._paused = speed <= 0
        self.packets: list[ParsedDataPacket] = []
        self.checkpoints: dict[int, SensorBuffers] = {0: SensorBuffers()}
        self.full_buffers = SensorBuffers(full_session_history=True)
        if not self._load_cache():
            checkpoint_builder = SensorBuffers(full_session_history=True)
            packet_source = (
                _decode_packets_for_replay(path)
                if os.path.exists(path)
                else [
                    parse_data_packet(_normalize_replay_payload(raw), idx)
                    for idx, raw in enumerate(read_dat_file(path))
                ]
            )
            for idx, pkt in enumerate(packet_source):
                self.packets.append(pkt)
                checkpoint_builder.feed(pkt, now=float(idx + 1))
                next_index = idx + 1
                if next_index % self.CHECKPOINT_INTERVAL == 0:
                    self.checkpoints[next_index] = checkpoint_builder.clone(compact=True)
            self.full_buffers = checkpoint_builder.clone()
            self._save_cache()
        self.buffers = SensorBuffers()
        self.current_index = 0
        if speed <= 0 and self.packets:
            self.seek(len(self.packets))

    def _cache_path(self) -> str:
        abs_path = os.path.abspath(self.path)
        if os.path.exists(self.path):
            stat = os.stat(self.path)
            key = f"{abs_path}::{stat.st_size}::{int(stat.st_mtime)}"
        else:
            key = abs_path
        digest = sha1(key.encode("utf-8")).hexdigest()[:16]
        return os.path.join("/tmp", f"watchpat_replay_{digest}.pkl")

    def _load_cache(self) -> bool:
        if not self.use_cache:
            return False
        cache_path = self._cache_path()
        if not os.path.exists(cache_path):
            return False
        try:
            with open(cache_path, "rb") as fh:
                payload = pickle.load(fh)
            if payload.get("cache_version") != REPLAY_CACHE_VERSION:
                return False
            self.packets = payload["packets"]
            self.checkpoints = {
                idx: SensorBuffers.from_serialized_state(state)
                for idx, state in payload["checkpoints"].items()
            }
            self.full_buffers = SensorBuffers.from_serialized_state(payload["full_buffers"])
            return True
        except Exception:
            logger.exception("Failed to load replay cache: %s", cache_path)
            return False

    def _save_cache(self):
        if not self.use_cache:
            return
        cache_path = self._cache_path()
        try:
            payload = {
                "cache_version": REPLAY_CACHE_VERSION,
                "packets": self.packets,
                "checkpoints": {
                    idx: buf.serialize_state()
                    for idx, buf in self.checkpoints.items()
                },
                "full_buffers": self.full_buffers.serialize_state(),
            }
            with open(cache_path, "wb") as fh:
                pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            logger.exception("Failed to save replay cache: %s", cache_path)

    @property
    def packet_count(self) -> int:
        return len(self.packets)

    @property
    def paused(self) -> bool:
        return self._paused

    def toggle_paused(self):
        self._paused = not self._paused
        return self._paused

    def seek(self, target_index: int):
        target_index = max(0, min(int(target_index), self.packet_count))
        checkpoint_index = (
            target_index // self.CHECKPOINT_INTERVAL) * self.CHECKPOINT_INTERVAL
        new_buffers = self.checkpoints.get(checkpoint_index, SensorBuffers()).clone()
        for idx in range(checkpoint_index, target_index):
            new_buffers.feed(self.packets[idx], now=float(idx + 1))
        self.buffers = new_buffers
        self.current_index = target_index
        self._playhead = float(target_index)
        return self.buffers

    def advance(self, packets_per_second: float):
        if self._paused or self.packet_count == 0 or packets_per_second <= 0:
            return self.buffers
        self._playhead = min(self.packet_count, self._playhead + packets_per_second)
        target_index = int(self._playhead)
        while self.current_index < target_index:
            self.buffers.feed(
                self.packets[self.current_index],
                now=float(self.current_index + 1),
            )
            self.current_index += 1
        if self.current_index >= self.packet_count:
            self._paused = True
        return self.buffers


# ---------------------------------------------------------------------------
# Dashboard figure
# ---------------------------------------------------------------------------
class WatchPATDashboard:
    """Matplotlib-based real-time dashboard."""

    def __init__(self, buffers: SensorBuffers):
        self.buffers = buffers
        self.anim = None
        self._closing = False
        self._wave_y_limits: dict[str, tuple[float, float]] = {}
        self._wave_y_history: dict[str, deque[tuple[float, float]]] = {}
        self._wave_y_last_packet: dict[str, int] = {}
        self._wave_y_last_update_packet: dict[str, int] = {}
        self._replay_button_icon_artists: list = []
        self._replay_button_is_paused = False
        self.replay_controller: Optional[ReplayController] = None
        self._pending_replay_queue: Optional[Queue] = None
        self._pending_replay_label = ""
        self.replay_slider = None
        self.replay_button = None
        self.replay_status_text = None
        self._ignore_slider_change = False
        self._loading_tick = 0
        self._build_figure()

    def _target_wave_ylim(self, data: np.ndarray) -> tuple[float, float]:
        lo = float(np.min(data))
        hi = float(np.max(data))
        margin = max((hi - lo) * 0.1, 1.0)
        return lo - margin, hi + margin

    def _wave_ylim_for(self, name: str, data: np.ndarray, packet_count: int) -> tuple[float, float]:
        target_lo, target_hi = self._target_wave_ylim(data)
        history = self._wave_y_history.setdefault(
            name, deque(maxlen=WAVEFORM_Y_ENVELOPE_PACKETS)
        )
        if self._wave_y_last_packet.get(name) != packet_count:
            history.append((target_lo, target_hi))
            self._wave_y_last_packet[name] = packet_count
        if history:
            env_lo = min(lo for lo, _ in history)
            env_hi = max(hi for _, hi in history)
        else:
            env_lo, env_hi = target_lo, target_hi

        current = self._wave_y_limits.get(name)
        if current is None:
            self._wave_y_limits[name] = (env_lo, env_hi)
            self._wave_y_last_update_packet[name] = packet_count
            return env_lo, env_hi

        cur_lo, cur_hi = current

        # Expand immediately when the recent packet envelope exceeds the current
        # display range so we never clip real motion.
        cur_lo = min(cur_lo, env_lo)
        cur_hi = max(cur_hi, env_hi)

        last_update_packet = self._wave_y_last_update_packet.get(name, packet_count)
        enough_new_packets = (
            packet_count - last_update_packet >= WAVEFORM_Y_UPDATE_EVERY_PACKETS
        )
        if len(data) >= BUFFER_SIZE and enough_new_packets:
            cur_lo += (env_lo - cur_lo) * WAVEFORM_Y_SHRINK_ALPHA
            cur_hi += (env_hi - cur_hi) * WAVEFORM_Y_SHRINK_ALPHA
            self._wave_y_last_update_packet[name] = packet_count

        self._wave_y_limits[name] = (cur_lo, cur_hi)
        return cur_lo, cur_hi

    def _reset_wave_y_scaling(self):
        """Drop cached waveform y-scale state after a discontinuous jump."""
        self._wave_y_limits.clear()
        self._wave_y_history.clear()
        self._wave_y_last_packet.clear()
        self._wave_y_last_update_packet.clear()

    def _set_replay_button_icon(self, paused: bool):
        self._replay_button_is_paused = paused
        if len(self._replay_button_icon_artists) != 3:
            return
        play_icon, pause_left, pause_right = self._replay_button_icon_artists
        play_icon.set_visible(paused)
        pause_left.set_visible(not paused)
        pause_right.set_visible(not paused)

    def _build_figure(self):
        _configure_windows_taskbar_icon()
        self.fig = plt.figure(
            figsize=(16, 9),
            facecolor="#1a1a2e",
        )
        self.fig.canvas.manager.set_window_title("WatchPAT ONE Dashboard")
        _apply_window_icon(self.fig)

        # Left 30% = compact waveform strips (cols 0-2)
        # Right 70% = readout bar (rows 0-1) + dominant sleep overview (rows 2-7)
        # Row 7 has height ratio 4 so the sleep overview gets ~75% of vertical space
        gs = GridSpec(
            8, 10, figure=self.fig,
            left=0.05, right=0.98, top=0.94, bottom=0.04,
            hspace=0.6, wspace=0.4,
            height_ratios=[1, 1, 1, 1, 1, 1, 1, 4],
        )
        LEFT = slice(None, 3)
        RIGHT = slice(3, None)

        dark_bg = "#16213e"
        grid_color = "#2a2a4a"
        text_color = "#e0e0e0"

        # -- Left strip: compact waveform plots (rows 0-3) --
        self.wave_axes = {}
        self.wave_lines = {}
        channels = [
            ("OxiA",  "Oximetry A",  0),
            ("OxiB",  "Oximetry B",  1),
            ("PAT",   "PAT Signal",  2),
            ("Chest", "Chest/Resp",  3),
        ]
        for name, title, row in channels:
            ax = self.fig.add_subplot(gs[row, LEFT])
            ax.set_facecolor(dark_bg)
            ax.set_title(title, color=text_color, fontsize=7,
                         fontweight="bold", loc="left", pad=2)
            ax.tick_params(colors=text_color, labelsize=6)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            for spine in ax.spines.values():
                spine.set_color(grid_color)
            ax.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
            ax.set_xlim(0, BUFFER_SIZE)
            ax.set_xticklabels([])
            ax.set_yticklabels([])
            line, = ax.plot([], [], color=CHANNEL_COLORS[name],
                            linewidth=0.8, antialiased=True)
            self.wave_axes[name] = ax
            self.wave_lines[name] = line

        # -- HR trend (row 4, left) --
        self.ax_hr_trend = self.fig.add_subplot(gs[4, LEFT])
        self.ax_hr_trend.set_facecolor(dark_bg)
        self.ax_hr_trend.set_title("Heart Rate (BPM)", color=text_color,
                                    fontsize=7, fontweight="bold",
                                    loc="left", pad=2)
        self.ax_hr_trend.tick_params(colors=text_color, labelsize=6)
        self.ax_hr_trend.spines["top"].set_visible(False)
        self.ax_hr_trend.spines["right"].set_visible(False)
        for spine in self.ax_hr_trend.spines.values():
            spine.set_color(grid_color)
        self.ax_hr_trend.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
        self.ax_hr_trend.set_xlim(0, DERIVED_HISTORY)
        self.ax_hr_trend.set_ylim(40, 120)
        self.ax_hr_trend.set_xticklabels([])
        self.ax_hr_trend.set_yticklabels([])
        self.hr_trend_line, = self.ax_hr_trend.plot(
            [], [], color="#e74c3c", linewidth=1.2)

        # -- SpO2 trend (row 5, left) --
        self.ax_spo2_trend = self.fig.add_subplot(gs[5, LEFT])
        self.ax_spo2_trend.set_facecolor(dark_bg)
        self.ax_spo2_trend.set_title("SpO₂ (%)", color=text_color,
                                      fontsize=7, fontweight="bold",
                                      loc="left", pad=2)
        self.ax_spo2_trend.tick_params(colors=text_color, labelsize=6)
        self.ax_spo2_trend.spines["top"].set_visible(False)
        self.ax_spo2_trend.spines["right"].set_visible(False)
        for spine in self.ax_spo2_trend.spines.values():
            spine.set_color(grid_color)
        self.ax_spo2_trend.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
        self.ax_spo2_trend.set_xlim(0, DERIVED_HISTORY)
        self.ax_spo2_trend.set_ylim(85, 100)
        self.ax_spo2_trend.set_xticklabels([])
        self.ax_spo2_trend.set_yticklabels([])
        self.spo2_trend_line, = self.ax_spo2_trend.plot(
            [], [], color="#3498db", linewidth=1.2)

        # -- Accelerometer (row 6, left) --
        self.ax_accel = self.fig.add_subplot(gs[6, LEFT])
        self.ax_accel.set_facecolor(dark_bg)
        self.ax_accel.set_title("Accelerometer", color=text_color,
                                fontsize=7, fontweight="bold",
                                loc="left", pad=2)
        self.ax_accel.tick_params(colors=text_color, labelsize=6)
        for spine in self.ax_accel.spines.values():
            spine.set_color(grid_color)
        self.ax_accel.spines["top"].set_visible(False)
        self.ax_accel.spines["right"].set_visible(False)
        self.ax_accel.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
        self.ax_accel.set_xlim(0, MOTION_BUFFER)
        self.ax_accel.set_xticklabels([])
        self.ax_accel.set_yticklabels([])
        self.accel_lines = {
            "x": self.ax_accel.plot([], [], color="#e74c3c", linewidth=0.8,
                                     label="X")[0],
            "y": self.ax_accel.plot([], [], color="#2ecc71", linewidth=0.8,
                                     label="Y")[0],
            "z": self.ax_accel.plot([], [], color="#3498db", linewidth=0.8,
                                     label="Z")[0],
        }

        # -- Session stats (row 7, left) --
        self.ax_stats = self.fig.add_subplot(gs[7, LEFT])
        self.ax_stats.set_facecolor(dark_bg)
        self.ax_stats.set_xlim(0, 1)
        self.ax_stats.set_ylim(0, 1)
        self.ax_stats.axis("off")
        self.ax_stats.set_title("Session", color=text_color,
                                fontsize=8, fontweight="bold", pad=4)
        self.stats_text = self.ax_stats.text(
            0.05, 0.95, "", fontsize=8, color=text_color,
            fontfamily="monospace", verticalalignment="top",
            transform=self.ax_stats.transAxes)

        # -- Right panel: readout bar (rows 0-1) --

        # Heart Rate readout (cols 3-5)
        self.ax_hr = self.fig.add_subplot(gs[0:2, 3:6])
        self.ax_hr.set_facecolor(dark_bg)
        self.ax_hr.set_xlim(0, 1)
        self.ax_hr.set_ylim(0, 1)
        self.ax_hr.axis("off")
        self.ax_hr.set_title("Heart Rate", color=text_color,
                              fontsize=9, fontweight="bold", pad=4)
        self.hr_value_text = self.ax_hr.text(
            0.5, 0.55, "--", ha="center", va="center",
            fontsize=36, fontweight="bold", color="#e74c3c",
            transform=self.ax_hr.transAxes)
        self.hr_unit_text = self.ax_hr.text(
            0.5, 0.15, "BPM", ha="center", va="center",
            fontsize=11, color=text_color,
            transform=self.ax_hr.transAxes)

        # SpO2 readout (cols 6-7)
        self.ax_spo2 = self.fig.add_subplot(gs[0:2, 6:8])
        self.ax_spo2.set_facecolor(dark_bg)
        self.ax_spo2.set_xlim(0, 1)
        self.ax_spo2.set_ylim(0, 1)
        self.ax_spo2.axis("off")
        self.ax_spo2.set_title("SpO2", color=text_color,
                                fontsize=9, fontweight="bold", pad=4)
        self.spo2_value_text = self.ax_spo2.text(
            0.5, 0.55, "--", ha="center", va="center",
            fontsize=36, fontweight="bold", color="#3498db",
            transform=self.ax_spo2.transAxes)
        self.spo2_unit_text = self.ax_spo2.text(
            0.5, 0.15, "%", ha="center", va="center",
            fontsize=11, color=text_color,
            transform=self.ax_spo2.transAxes)

        # Body position (cols 8-9)
        self.ax_pos = self.fig.add_subplot(gs[0:2, 8:10])
        self.ax_pos.set_facecolor(dark_bg)
        self.ax_pos.set_xlim(0, 1)
        self.ax_pos.set_ylim(0, 1)
        self.ax_pos.axis("off")
        self.ax_pos.set_title("Body Position", color=text_color,
                              fontsize=9, fontweight="bold", pad=4)
        self.pos_text = self.ax_pos.text(
            0.5, 0.55, "?", ha="center", va="center",
            fontsize=28, fontweight="bold", color="#f1c40f",
            transform=self.ax_pos.transAxes)
        self.pos_label = self.ax_pos.text(
            0.5, 0.15, "Waiting...", ha="center", va="center",
            fontsize=11, color=text_color,
            transform=self.ax_pos.transAxes)

        # Title bar
        self.title_text = self.fig.text(
            0.5, 0.97, "WatchPAT ONE — Sensor Dashboard",
            ha="center", va="center", fontsize=13,
            fontweight="bold", color="#ecf0f1",
        )
        self.mode_text = self.fig.text(
            0.98, 0.97, "", ha="right", va="center", fontsize=9,
            color="#95a5a6",
        )

        # -- Sleep overview timeline (rows 2-7, full right width) --
        # Split the right panel: main overview on top, stage colour strip below (no gap)
        _right_inner = GridSpecFromSubplotSpec(
            2, 1, subplot_spec=gs[2:, RIGHT],
            height_ratios=[12, 1], hspace=0,
        )
        self.ax_apnea = self.fig.add_subplot(_right_inner[0])
        self.ax_apnea.set_facecolor(dark_bg)
        self.ax_apnea.tick_params(colors=text_color, labelsize=7)
        self.ax_apnea.spines["top"].set_visible(False)
        for spine in self.ax_apnea.spines.values():
            spine.set_color(grid_color)
        self.ax_apnea.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
        self.ax_apnea.set_ylim(80, 102)
        self.ax_apnea.set_xticklabels([])
        self.ax_apnea.set_ylabel("SpO₂ (%)", fontsize=7, color="#3498db")
        self.ax_apnea.tick_params(axis="y", colors="#3498db")
        self.ax_apnea.set_title(
            "Sleep Overview",
            color=text_color, fontsize=9, fontweight="bold", loc="left", pad=4)
        self.spo2_full_line, = self.ax_apnea.plot(
            [], [], color="#5dade2", linewidth=1.8, alpha=0.95)
        self.ax_apnea.text(
            0.01, 0.97, "SpO2 line",
            ha="left", va="top", color="#5dade2", fontsize=8,
            transform=self.ax_apnea.transAxes)
        self.ax_apnea.text(
            0.13, 0.97, "Gray bars = respiratory events / 5 min",
            ha="left", va="top", color=text_color, fontsize=8,
            transform=self.ax_apnea.transAxes)
        self.ax_apnea.text(
            0.44, 0.97, "Stages:",
            ha="left", va="top", color=text_color, fontsize=8,
            transform=self.ax_apnea.transAxes)
        stage_legend = [
            (0.50, "Awake", SLEEP_STAGE_COLORS[SLEEP_STAGE_AWAKE]),
            (0.59, "Light", SLEEP_STAGE_COLORS[SLEEP_STAGE_LIGHT]),
            (0.67, "Deep", SLEEP_STAGE_COLORS[SLEEP_STAGE_DEEP]),
            (0.75, "REM", SLEEP_STAGE_COLORS[SLEEP_STAGE_REM]),
        ]
        for xpos, label, color in stage_legend:
            self.ax_apnea.text(
                xpos, 0.97, "■",
                ha="left", va="top", color=color, fontsize=9,
                transform=self.ax_apnea.transAxes)
            self.ax_apnea.text(
                xpos + 0.012, 0.97, label,
                ha="left", va="top", color=text_color, fontsize=8,
                transform=self.ax_apnea.transAxes)
        self._drawn_evt_count: dict[str, int] = {t: 0 for t in EVENT_COLORS}
        self.ax_event_burden = self.ax_apnea.twinx()
        self.ax_event_burden.set_facecolor("none")
        self.ax_event_burden.set_ylim(0, 8)
        self.ax_event_burden.set_ylabel("Events / 5 min", fontsize=7,
                                        color="#f5f6fa")
        self.ax_event_burden.tick_params(axis="y", colors="#f5f6fa", labelsize=7)
        self.ax_event_burden.spines["right"].set_color("#f5f6fa")
        self.ax_event_burden.spines["top"].set_visible(False)
        self.ax_event_burden.spines["left"].set_visible(False)
        self.event_bars = self.ax_event_burden.bar(
            [], [], width=4.2, align="center",
            color="#c7d0dd", alpha=0.55, linewidth=0)
        self.event_bar_patches = list(self.event_bars.patches)

        self.ax_sleep_stage = self.ax_apnea.twinx()
        self.ax_sleep_stage.set_facecolor("none")
        self.ax_sleep_stage.set_ylim(-0.5, 3.5)
        self.ax_sleep_stage.set_yticks([0, 1, 2, 3])
        self.ax_sleep_stage.set_yticklabels(
            ["Deep", "Light", "REM", "Awake"], color="#ecf0f1", fontsize=7)
        self.ax_sleep_stage.set_ylabel("Sleep stage", fontsize=7, color="#ecf0f1")
        self.ax_sleep_stage.tick_params(axis="y", colors="#ecf0f1", labelsize=7)
        self.ax_sleep_stage.spines["right"].set_position(("outward", 42))
        self.ax_sleep_stage.spines["right"].set_color("#ecf0f1")
        self.ax_sleep_stage.spines["top"].set_visible(False)
        self.ax_sleep_stage.spines["left"].set_visible(False)
        self.ax_sleep_stage.set_facecolor((0, 0, 0, 0))
        self.sleep_stage_line, = self.ax_sleep_stage.step(
            [], [], where="post", color="#ffffff", linewidth=2.4, alpha=0.95)
        self.sleep_stage_line.set_alpha(0.0)
        self.stage_strip = self.fig.add_subplot(_right_inner[1], sharex=self.ax_apnea)
        self.stage_strip.set_facecolor("#111827")
        self.stage_strip.set_ylim(0, 1)
        self.stage_strip.set_yticks([])
        self.stage_strip.set_ylabel("Stage", fontsize=7, color="#ecf0f1", labelpad=8)
        self.stage_strip.set_xlabel("Elapsed (min)", fontsize=7, color=text_color)
        self.stage_strip.tick_params(axis="x", colors=text_color, labelsize=7)
        self.stage_strip.spines["top"].set_color("#34495e")
        self.stage_strip.spines["right"].set_visible(False)
        self.stage_strip.spines["left"].set_color(grid_color)
        self.stage_strip.spines["bottom"].set_color(grid_color)
        self.stage_strip_image = self.stage_strip.imshow(
            SLEEP_STAGE_BG_RGBA.reshape(1, 1, 4),
            extent=[0, 1, 0, 1],
            aspect="auto",
            origin="lower",
            interpolation="nearest",
        )
        self.overview_playhead_line = self.ax_apnea.axvline(
            x=0.0, color="#ecf0f1", linewidth=1.2, alpha=0.85, linestyle="--")
        self.stage_playhead_line = self.stage_strip.axvline(
            x=0.0, color="#ecf0f1", linewidth=1.2, alpha=0.85, linestyle="--")

        self.ax_apnea_ahi_text = self.ax_apnea.text(
            0.99, 1.05, "pAHI: --",
            ha="right", va="bottom", fontsize=9, fontweight="bold",
            color="#7f8c8d", transform=self.ax_apnea.transAxes)

        self._loading_text = self.fig.text(
            0.5, 0.5, "",
            ha="center", va="center",
            fontsize=18, fontweight="bold",
            color="#ecf0f1",
            visible=False,
            zorder=10,
            bbox=dict(
                boxstyle="round,pad=0.7",
                facecolor="#0f0f23",
                edgecolor="#4a4a8a",
                alpha=0.92,
                linewidth=1.5,
            ),
        )

        self.fig.canvas.mpl_connect("close_event", self._on_close)
        manager = getattr(self.fig.canvas, "manager", None)
        window = getattr(manager, "window", None)
        protocol = getattr(window, "protocol", None)
        if callable(protocol):
            protocol("WM_DELETE_WINDOW", self.request_close)

    def attach_replay_loader(self, replay_queue: Queue, ready_label: str):
        """Allow replay preprocessing to finish in the background."""
        self._pending_replay_queue = replay_queue
        self._pending_replay_label = ready_label

    def enable_replay_scrubber(self, controller: ReplayController):
        """Attach replay controls for seeking through preloaded data."""
        self.replay_controller = controller
        self.buffers = controller.buffers
        text_color = "#e0e0e0"

        # Pause button floats inside the sleep overview — no margin adjustment needed
        _btn_ax = self.ax_apnea.inset_axes([0.928, 0.905, 0.065, 0.095])
        _btn_ax.set_facecolor("none")
        _btn_ax.set_aspect("equal", adjustable="box")
        _btn_ax.set_xticks([])
        _btn_ax.set_yticks([])
        for spine in _btn_ax.spines.values():
            spine.set_visible(False)
        _btn_ax.add_patch(
            Circle(
                (0.5, 0.5),
                0.48,
                transform=_btn_ax.transAxes,
                facecolor="#2a2a4a",
                edgecolor="#4a4a8a",
                linewidth=1.2,
            )
        )
        play_icon = Polygon(
            [(0.42, 0.32), (0.42, 0.68), (0.70, 0.50)],
            closed=True,
            transform=_btn_ax.transAxes,
            facecolor=text_color,
            edgecolor="none",
        )
        pause_left = Rectangle(
            (0.37, 0.30),
            0.10,
            0.40,
            transform=_btn_ax.transAxes,
            facecolor=text_color,
            edgecolor="none",
        )
        pause_right = Rectangle(
            (0.53, 0.30),
            0.10,
            0.40,
            transform=_btn_ax.transAxes,
            facecolor=text_color,
            edgecolor="none",
        )
        _btn_ax.add_patch(play_icon)
        _btn_ax.add_patch(pause_left)
        _btn_ax.add_patch(pause_right)
        self._replay_button_icon_artists = [play_icon, pause_left, pause_right]
        self.replay_button = Button(
            _btn_ax,
            "",
            color="none",
            hovercolor="none",
        )
        self.replay_button.label.set_visible(False)
        _btn_ax.patch.set_alpha(0.0)
        self._set_replay_button_icon(controller.paused)

        # Status text floats near the bottom of the sleep overview
        self.replay_status_text = self.ax_apnea.text(
            0.01, 0.03, "",
            ha="left", va="bottom", fontsize=8, color=text_color,
            transform=self.ax_apnea.transAxes, zorder=10,
        )

        def _on_button(_):
            paused = controller.toggle_paused()
            self._set_replay_button_icon(paused)
            if self.fig.canvas:
                self.fig.canvas.draw_idle()

        # twinx axes overlay ax_apnea and capture clicks in the main graph area
        _seekable = frozenset({
            self.ax_apnea, self.stage_strip,
            self.ax_event_burden, self.ax_sleep_stage,
        })

        def _on_graph_click(event):
            if event.inaxes not in _seekable:
                return
            if event.xdata is None:
                return
            target_idx = int(float(event.xdata) * 60.0)
            controller.seek(target_idx)
            self.buffers = controller.buffers
            self._reset_wave_y_scaling()
            if self.fig.canvas:
                self.fig.canvas.draw_idle()

        self.replay_button.on_clicked(_on_button)
        self.fig.canvas.mpl_connect("button_press_event", _on_graph_click)
        self._sync_replay_controls()

    def _sync_replay_controls(self):
        if not self.replay_controller:
            return
        ctrl = self.replay_controller
        if self.replay_slider is not None:
            self._ignore_slider_change = True
            self.replay_slider.set_val(ctrl.current_index)
            self._ignore_slider_change = False
        if self.replay_button is not None:
            self._set_replay_button_icon(ctrl.paused)
        if self.replay_status_text is not None:
            total = max(ctrl.packet_count, 1)
            cur_min = ctrl.current_index / 60.0
            total_min = ctrl.packet_count / 60.0
            self.replay_status_text.set_text(
                f"{cur_min:.1f}/{total_min:.1f}m\n{ctrl.current_index}/{total}"
            )

    def _reset_event_lines(self):
        for evt_type in self._drawn_evt_count:
            self._drawn_evt_count[evt_type] = 0

    def _set_bar_container(self, container, x_values, heights, bottoms):
        patches = self.event_bar_patches
        color = patches[0].get_facecolor() if patches else "#ffffff"
        while len(patches) < len(x_values):
            extra = self.ax_event_burden.bar(
                [0], [0], width=4.2, align="center",
                color=color, alpha=0.55, linewidth=0)
            patches.extend(extra.patches)
        for idx, patch in enumerate(patches):
            if idx < len(x_values):
                patch.set_x(float(x_values[idx]) - 2.1)
                patch.set_width(4.2)
                patch.set_y(float(bottoms[idx]))
                patch.set_height(float(heights[idx]))
                patch.set_visible(True)
            else:
                patch.set_visible(False)

    def update(self, frame):
        """Animation update callback."""
        if self.replay_controller is None and self._pending_replay_queue is not None:
            try:
                status, payload = self._pending_replay_queue.get_nowait()
            except Empty:
                status = None
                payload = None
            if status == "ready":
                self.enable_replay_scrubber(payload)
                self.set_mode_label(self._pending_replay_label)
                self._pending_replay_queue = None
                self._loading_text.set_visible(False)
            elif status == "error":
                self.set_mode_label(f"Replay load failed: {payload}")
                self._pending_replay_queue = None
                self._loading_text.set_visible(False)
            else:
                self._loading_tick += 1
                s = _SPINNER[(self._loading_tick // 3) % len(_SPINNER)]
                self._loading_text.set_text(f"  {s}  Analyzing recording…  ")
                self._loading_text.set_visible(True)

        if self.replay_controller is not None:
            packets_per_second = (self.replay_controller.speed
                                  if self.replay_controller.speed > 0 else 0.0)
            self.replay_controller.advance(packets_per_second / FPS)
            self.buffers = self.replay_controller.buffers
            self._sync_replay_controls()
        snap = self.buffers.snapshot()
        overview_snap = snap
        overview_total_min = None
        overview_playhead_min = None
        if self.replay_controller is not None:
            full_buffers = getattr(self.replay_controller, "full_buffers", None)
            if isinstance(full_buffers, SensorBuffers):
                overview_snap = full_buffers.snapshot()
            overview_total_min = self.replay_controller.packet_count / 60.0
            # Use the float _playhead so the position indicator moves every frame
            overview_playhead_min = self.replay_controller._playhead / 60.0

        # Pre-fetch leading samples from the next unprocessed packet so waveforms
        # scroll smoothly between packet boundaries (replay only).
        _wf_ahead: dict[str, np.ndarray] = {}
        if self.replay_controller is not None:
            ctrl = self.replay_controller
            frac = ctrl._playhead - ctrl.current_index  # 0.0–1.0
            if frac > 0 and ctrl.current_index < ctrl.packet_count:
                for wf in ctrl.packets[ctrl.current_index].waveforms:
                    ch = wf.channel_name
                    n_ahead = int(frac * len(wf.samples))
                    if ch not in _wf_ahead and n_ahead > 0 and wf.samples:
                        _wf_ahead[ch] = np.array(
                            wf.samples[:n_ahead], dtype=float)

        # -- Update waveforms --
        _ch_keys = [("OxiA", "oxi_a"), ("OxiB", "oxi_b"),
                    ("PAT", "pat"), ("Chest", "chest")]
        for name, key in _ch_keys:
            buf_data = snap[key]
            ahead = _wf_ahead.get(name)
            if ahead is not None and len(ahead) > 0:
                combined = np.concatenate([buf_data, ahead])
                display_data = combined[-BUFFER_SIZE:]
            else:
                display_data = buf_data
            line = self.wave_lines[name]
            ax = self.wave_axes[name]
            if len(display_data) > 0:
                x_start = max(0, BUFFER_SIZE - len(display_data))
                x = np.arange(x_start, x_start + len(display_data))
                line.set_data(x, display_data)
                ax.set_xlim(0, BUFFER_SIZE)
                # Prefer the committed buffer for y-limits so future samples
                # do not jerk the axis around at packet boundaries, but fall
                # back to the visible data while the replay is still before the
                # first fully committed packet.
                ylim_source = buf_data if len(buf_data) > 0 else display_data
                ax.set_ylim(*self._wave_ylim_for(name, ylim_source, snap["packet_count"]))
            else:
                line.set_data([], [])

        # -- Update accelerometer --
        for axis_name, key in [("x", "accel_x"), ("y", "accel_y"),
                                ("z", "accel_z")]:
            data = snap[key]
            line = self.accel_lines[axis_name]
            if len(data) > 0:
                x = np.arange(len(data))
                line.set_data(x, data)
            else:
                line.set_data([], [])
        all_accel = np.concatenate([
            snap["accel_x"], snap["accel_y"], snap["accel_z"]
        ]) if len(snap["accel_x"]) > 0 else np.array([0])
        if len(all_accel) > 0:
            lo, hi = np.min(all_accel), np.max(all_accel)
            margin = max((hi - lo) * 0.1, 50)
            self.ax_accel.set_ylim(lo - margin, hi + margin)
            self.ax_accel.set_xlim(
                0, max(len(snap["accel_x"]), MOTION_BUFFER))

        # -- Update HR/SpO2 readouts --
        hr = snap["current_hr"]
        if hr > 0:
            self.hr_value_text.set_text(f"{int(round(hr))}")
            if hr > 100:
                self.hr_value_text.set_color("#e74c3c")
            elif hr < 50:
                self.hr_value_text.set_color("#f39c12")
            else:
                self.hr_value_text.set_color("#2ecc71")
        else:
            self.hr_value_text.set_text("--")
            self.hr_value_text.set_color("#7f8c8d")

        spo2 = snap["current_spo2"]
        if spo2 > 0:
            self.spo2_value_text.set_text(f"{int(round(spo2))}")
            if spo2 < 90:
                self.spo2_value_text.set_color("#e74c3c")
            elif spo2 < 94:
                self.spo2_value_text.set_color("#f39c12")
            else:
                self.spo2_value_text.set_color("#3498db")
        else:
            self.spo2_value_text.set_text("--")
            self.spo2_value_text.set_color("#7f8c8d")

        # -- Update HR trend --
        hr_data = snap["hr_history"]
        if len(hr_data) > 0:
            x = np.arange(len(hr_data))
            self.hr_trend_line.set_data(x, hr_data)
            self.ax_hr_trend.set_xlim(0, max(len(hr_data), DERIVED_HISTORY))
            valid = hr_data[~np.isnan(hr_data)]
            if len(valid) > 0:
                lo, hi = float(np.min(valid)), float(np.max(valid))
                margin = max((hi - lo) * 0.15, 5)
                self.ax_hr_trend.set_ylim(
                    max(30, lo - margin), min(180, hi + margin))
        else:
            self.hr_trend_line.set_data([], [])

        # -- Update SpO2 trend --
        spo2_data = snap["spo2_history"]
        if len(spo2_data) > 0:
            x, spo2_valid = _valid_spo2_xy(np.arange(len(spo2_data)), spo2_data)
            self.spo2_trend_line.set_data(x, spo2_valid)
            self.ax_spo2_trend.set_xlim(0, max(len(spo2_data), DERIVED_HISTORY))
            valid = spo2_valid
            if len(valid) > 0:
                lo, hi = float(np.min(valid)), float(np.max(valid))
                margin = max((hi - lo) * 0.15, 1)
                self.ax_spo2_trend.set_ylim(
                    max(60, lo - margin), min(100, hi + margin))
        else:
            self.spo2_trend_line.set_data([], [])

        # -- Update body position --
        pos = snap["body_position"]
        pos_label = snap["body_position_label"]
        pos_colors = {
            "Supine": "#e74c3c", "Prone": "#e67e22",
            "Left": "#3498db", "Right": "#2ecc71",
            "Upright": "#9b59b6", "Inverted": "#f39c12",
        }
        color = pos_colors.get(pos_label, "#f1c40f")
        self.pos_text.set_text(pos_label)
        self.pos_text.set_color(color)
        axis_str = f"({pos})" if pos != "?" else ""
        self.pos_label.set_text(axis_str)

        # -- Update apnea / PAT events panel --
        spo2_times = overview_snap["spo2_full_times"]
        spo2_full = overview_snap["spo2_full_history"]
        t_max_min = 1.0
        if len(spo2_times) > 0:
            t_min = spo2_times / 60.0
            t_min_valid, spo2_valid = _valid_spo2_xy(t_min, spo2_full)
            t_max_min = max(float(t_min[-1]), 1.0)
            if overview_total_min is not None:
                t_max_min = max(float(overview_total_min), 1.0)
            t_min_smooth, spo2_smooth = _smooth_and_downsample(
                t_min_valid, spo2_valid, window=21, max_points=700)
            self.spo2_full_line.set_data(t_min_smooth, spo2_smooth)
            self.ax_apnea.set_xlim(0, t_max_min)
            valid_spo2 = spo2_valid
            if len(valid_spo2) > 0:
                lo = max(60.0, float(np.min(valid_spo2)) - 2)
                self.ax_apnea.set_ylim(lo, 102)
        else:
            self.spo2_full_line.set_data([], [])

        stage_times = overview_snap["sleep_stage_times"]
        stage_values = overview_snap["sleep_stage_history"]
        if len(stage_times) > 0:
            stage_x = stage_times / 60.0
            self.sleep_stage_line.set_data(stage_x, stage_values)
            self.ax_sleep_stage.set_xlim(0, t_max_min)
            labels = overview_snap["sleep_stage_labels"]
            stage_pixels = max(1, min(600, int(t_max_min * 2)))
            rgba = np.tile(SLEEP_STAGE_BG_RGBA, (1, stage_pixels, 1))
            stage_edges = np.linspace(0.0, t_max_min, stage_pixels + 1)
            labels = list(labels)
            for pixel in range(stage_pixels):
                center = 0.5 * (stage_edges[pixel] + stage_edges[pixel + 1])
                idx = np.searchsorted(stage_x, center, side="right") - 1
                if idx < 0:
                    continue
                idx = min(idx, len(labels) - 1)
                color = matplotlib.colors.to_rgba(
                    SLEEP_STAGE_COLORS.get(labels[idx], "#95a5a6"), alpha=1.0)
                rgba[0, pixel, :] = color
            self.stage_strip_image.set_data(rgba)
            self.stage_strip_image.set_extent([0, t_max_min, 0, 1])
            self.stage_strip.set_xlim(0, t_max_min)
        else:
            self.sleep_stage_line.set_data([], [])
            self.stage_strip_image.set_data(SLEEP_STAGE_BG_RGBA.reshape(1, 1, 4))
            self.stage_strip_image.set_extent([0, 1, 0, 1])

        self._reset_event_lines()

        pat_events = overview_snap["pat_events"]
        bucket_edges = np.arange(0.0, t_max_min + 5.0, 5.0)
        if len(bucket_edges) < 2:
            bucket_edges = np.array([0.0, 5.0])
        bucket_centers = 0.5 * (bucket_edges[:-1] + bucket_edges[1:])
        bucket_counts = {
            evt_type: np.zeros(len(bucket_centers), dtype=float)
            for evt_type in EVENT_COLORS
        }
        for ev_time, _, _, _, evt_type in pat_events:
            x = ev_time / 60.0
            bucket_idx = min(
                len(bucket_centers) - 1,
                max(0, np.searchsorted(bucket_edges, x, side="right") - 1),
            )
            bucket_counts[evt_type][bucket_idx] += 1
            self._drawn_evt_count[evt_type] += 1

        central_events = overview_snap["central_events"]
        for ev_time, _ in central_events:
            x = ev_time / 60.0
            bucket_idx = min(
                len(bucket_centers) - 1,
                max(0, np.searchsorted(bucket_edges, x, side="right") - 1),
            )
            bucket_counts[EVT_CENTRAL][bucket_idx] += 1
            self._drawn_evt_count[EVT_CENTRAL] += 1
        total_heights = np.zeros(len(bucket_centers), dtype=float)
        for evt_type in (EVT_APNEA, EVT_HYPOPNEA, EVT_RERA, EVT_CENTRAL):
            total_heights += bucket_counts[evt_type]
        self._set_bar_container(
            self.event_bars, bucket_centers, total_heights, np.zeros(len(bucket_centers), dtype=float))
        max_stack = max(1.0, float(np.max(total_heights)) if len(total_heights) > 0 else 1.0)
        self.ax_event_burden.set_xlim(0, t_max_min)
        self.ax_event_burden.set_ylim(0, max(4.0, max_stack * 1.2))
        playhead_min = overview_playhead_min if overview_playhead_min is not None else t_max_min
        self.overview_playhead_line.set_xdata([playhead_min, playhead_min])
        self.stage_playhead_line.set_xdata([playhead_min, playhead_min])

        # AHI/RDI text
        pahi = snap["pahi_estimate"]
        rdi  = snap["rdi_estimate"]
        ahi  = snap["ahi_estimate"]
        current_central_count = snap.get("central_event_count", 0)
        if current_central_count <= 0:
            current_central_count = len(snap["central_events"])
        n_central = current_central_count
        primary = pahi if pahi >= 0 else ahi
        if primary >= 0:
            n_a = self._drawn_evt_count[EVT_APNEA]
            n_h = self._drawn_evt_count[EVT_HYPOPNEA]
            n_r = self._drawn_evt_count[EVT_RERA]
            rdi_str = f"{rdi:.1f}" if rdi >= 0 else "--"
            label = (f"pAHI: {pahi:.1f}/hr  pRDI: {rdi_str}/hr"
                     f"  (A:{n_a} H:{n_h} R:{n_r} C:{n_central})")
            self.ax_apnea_ahi_text.set_text(label)
            if primary < 5:
                self.ax_apnea_ahi_text.set_color("#2ecc71")
            elif primary < 15:
                self.ax_apnea_ahi_text.set_color("#f39c12")
            else:
                self.ax_apnea_ahi_text.set_color("#e74c3c")
        else:
            self.ax_apnea_ahi_text.set_text("pAHI: --")
            self.ax_apnea_ahi_text.set_color("#7f8c8d")

        # -- Update stats panel --
        if self.replay_controller is not None:
            elapsed = float(self.replay_controller.current_index)
            pkt_rate = self.replay_controller.speed if self.replay_controller.speed > 0 else 0.0
        else:
            elapsed = time.time() - snap["start_time"] if snap["start_time"] else 0
            pkt_rate = (snap["packet_count"] / elapsed) if elapsed > 0 else 0
        kb = snap["total_bytes"] / 1024
        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        crc_ok, crc_total = snap["motion_crc"]
        crc_str = f"{crc_ok}/{crc_total}" if crc_total > 0 else "-"
        fa = snap["field_a"][-1] if len(snap["field_a"]) > 0 else 0
        fb = snap["field_b"][-1] if len(snap["field_b"]) > 0 else 0
        pahi_val = snap["pahi_estimate"]
        rdi_val  = snap["rdi_estimate"]
        pahi_str = f"{pahi_val:.1f}" if pahi_val >= 0.0 else "--"
        rdi_str  = f"{rdi_val:.1f}"  if rdi_val  >= 0.0 else "--"
        n_central_stat = current_central_count
        stage_pct = snap["sleep_stage_percentages"]
        current_stage = snap["current_sleep_stage"]
        stats_lines = [
            f"Packets:   {snap['packet_count']:>8d}",
            f"Data:      {kb:>7.1f} KB",
            f"Rate:      {pkt_rate:>7.1f} p/s",
            f"Elapsed:   {mins:>4d}m {secs:02d}s",
            f"pAHI:      {pahi_str:>5s} /hr",
            f"pRDI:      {rdi_str:>5s} /hr",
            f"Central:   {n_central_stat:>8d}",
            f"Stage:     {current_stage:>8s}",
            f"Stages %:  A {stage_pct.get('Awake', 0.0):>4.1f}"
            f" L {stage_pct.get('Light', 0.0):>4.1f}"
            f" D {stage_pct.get('Deep', 0.0):>4.1f}"
            f" R {stage_pct.get('REM', 0.0):>4.1f}",
            f"Metric:    {snap['metric']:>8d}",
            f"Motion:    A={fa} B={fb}",
            f"CRC:       {crc_str:>8s}",
        ]
        if snap["events"]:
            stats_lines.append(f"Event: {snap['events'][-1]}")
        self.stats_text.set_text("\n".join(stats_lines))

        # Return all artists that changed
        artists = list(self.wave_lines.values())
        artists.extend(self.accel_lines.values())
        artists.extend([
            self.hr_value_text, self.spo2_value_text,
            self.hr_trend_line, self.spo2_trend_line,
            self.pos_text, self.pos_label,
            self.stats_text,
            self.spo2_full_line,
            self.sleep_stage_line,
            self.ax_apnea_ahi_text,
            self.overview_playhead_line,
            self.stage_playhead_line,
        ])
        artists.append(self.stage_strip_image)
        artists.extend(self.event_bars.patches)
        return artists

    def set_mode_label(self, text: str):
        self.mode_text.set_text(text)

    def _on_close(self, _event):
        self._closing = True
        if self.anim and self.anim.event_source:
            self.anim.event_source.stop()

    def request_close(self):
        if self._closing:
            return
        self._closing = True
        if self.anim and self.anim.event_source:
            self.anim.event_source.stop()
        plt.close(self.fig)

    def run(self):
        """Start the animation loop (blocks until window closes)."""
        self.anim = FuncAnimation(
            self.fig, self.update, interval=1000 // FPS,
            blit=False, cache_frame_data=False,
        )
        plt.show()
        if self.anim and self.anim.event_source:
            self.anim.event_source.stop()


# ---------------------------------------------------------------------------
# Replay feeder — reads a .dat file and feeds packets at paced rate
# ---------------------------------------------------------------------------
def replay_feeder(path: str, buffers: SensorBuffers, speed: float = 1.0,
                  stop_event: threading.Event = None):
    """Feed packets from a .dat file into buffers at real-time pace."""
    for idx, raw in enumerate(read_dat_file(path)):
        if stop_event and stop_event.is_set():
            break
        pkt = parse_data_packet(_normalize_replay_payload(raw), idx)
        buffers.feed(pkt)
        # Each packet ≈ 1 second of data
        if speed > 0:
            time.sleep(1.0 / speed)
    logger.info("Replay complete: %d packets", idx + 1)


# ---------------------------------------------------------------------------
# Live BLE feeder — runs asyncio event loop in a thread
# ---------------------------------------------------------------------------
def ble_feeder(serial: str, buffers: SensorBuffers,
               scan_time: float = 10.0,
               stop_event: threading.Event = None,
               output_path: str = ""):
    """Connect to a WatchPAT device and feed live data into buffers."""

    async def _run():
        wp = WatchPATClient()

        logger.info("Scanning for WatchPAT devices (%0.fs)...", scan_time)
        devices = await wp.scan(
            timeout=scan_time,
            serial_filter=serial,
            stop_event=stop_event,
        )
        if stop_event and stop_event.is_set():
            logger.info("BLE feeder stopped during scan.")
            return
        if not devices:
            logger.error("No WatchPAT devices found.")
            return

        device = devices[0]
        logger.info("Connecting to %s ...", device.name)
        await wp.connect(device)
        if stop_event and stop_event.is_set():
            logger.info("BLE feeder stopped before session start.")
            return

        dump_file = None
        if output_path:
            dump_file = open(output_path, "wb")

        try:
            result = await wp.is_device_paired()
            logger.info("Paired: %s", result)

            config = await wp.start_session()
            if config:
                logger.info("Config: %s", config)

            def on_data(payload):
                if dump_file:
                    dump_file.write(struct.pack("<I", len(payload)))
                    dump_file.write(payload)
                    dump_file.flush()

            def on_parsed(pkt: ParsedDataPacket):
                buffers.feed(pkt)

            wp.on_data_packet = on_data
            wp.on_parsed_data = on_parsed

            acq = await wp.start_acquisition()
            logger.info("Acquisition started: %s", acq)

            while not (stop_event and stop_event.is_set()):
                await asyncio.sleep(0.5)

            logger.info("Stopping acquisition...")
            await wp.stop_acquisition()

        finally:
            if dump_file:
                dump_file.close()
            await wp.disconnect()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="WatchPAT ONE Real-Time Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --live                             Auto-detect device & start dashboard
  %(prog)s --serial XXXXXXXXX                Live BLE capture with dashboard
  %(prog)s --serial XXXXXXXXX -o out.dat     Live capture + save raw dump
  %(prog)s --replay capture.dat              Replay a capture at 1x speed
  %(prog)s --replay capture.dat --speed 10   Replay at 10x speed
""")
    parser.add_argument("--live", action="store_true",
                        help="Live BLE connection (auto-detect device)")
    parser.add_argument("--serial", type=str, default="",
                        help="Device serial for live BLE connection (implies --live)")
    parser.add_argument("--replay", type=str, default="",
                        help="Replay a .dat capture file")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Replay speed multiplier (default: 1.0, 0=max)")
    parser.add_argument("--output", "-o", type=str, default="",
                        help="Save raw data to file during live capture")
    parser.add_argument("--scan-time", type=float, default=10.0,
                        help="BLE scan duration in seconds")
    parser.add_argument("--nocache", action="store_true",
                        help="Disable replay cache load/save for .dat replay")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    # Default to live auto-detect if no mode specified
    if not args.replay and not args.serial and not args.live:
        args.live = True

    buffers = SensorBuffers()
    stop_event = threading.Event()
    dashboard = WatchPATDashboard(buffers)

    if args.replay:
        dashboard.set_mode_label(f"Replay: {args.replay}  (loading…)")
        replay_queue = Queue()
        dashboard.attach_replay_loader(
            replay_queue,
            f"Replay: {args.replay}  ({args.speed}x)",
        )
        _path, _speed, _cache = args.replay, args.speed, not args.nocache

        def _load_replay():
            try:
                ctrl = ReplayController(_path, _speed, use_cache=_cache)
                replay_queue.put(("ready", ctrl))
            except Exception as exc:
                replay_queue.put(("error", str(exc)))

        t = threading.Thread(target=_load_replay, daemon=True)
    else:
        out = args.output
        if not out:
            ts_str = time.strftime("%Y%m%d_%H%M%S")
            serial_tag = args.serial or "auto"
            out = f"watchpat_{serial_tag}_{ts_str}.dat"
        label = args.serial if args.serial else "auto-detect"
        dashboard.set_mode_label(f"Live: {label}")
        t = threading.Thread(
            target=ble_feeder,
            args=(args.serial, buffers, args.scan_time, stop_event, out),
            daemon=True,
        )

    if t is not None:
        t.start()

    try:
        dashboard.run()  # Blocks until window closed
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if t is not None:
            t.join(timeout=1.5)
        snap = dashboard.buffers.snapshot()
        print(f"\nSession summary: {snap['packet_count']} packets, "
              f"{snap['total_bytes']/1024:.1f} KB")


if __name__ == "__main__":
    main()
