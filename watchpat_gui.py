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
import logging
import math
import os
import struct
import sys
import threading
import time
from collections import deque
from queue import Queue, Empty
from typing import Optional

import matplotlib


def _configure_matplotlib_backend():
    """Prefer a native interactive backend and avoid forcing Tk on macOS."""
    backend_logger = logging.getLogger("watchpat.gui")
    if os.environ.get("MPLBACKEND"):
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
from matplotlib.patches import FancyBboxPatch, Circle
from matplotlib.gridspec import GridSpec
from matplotlib.widgets import Button, Slider
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
    SensorBuffers,
)

FPS = 15

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

def _normalize_replay_payload(raw: bytes) -> bytes:
    """Accept either a payload-only capture record or a full BLE packet."""
    if len(raw) >= HEADER_SIZE and raw[:2] == b"\xBB\xBB":
        return raw[HEADER_SIZE:]
    return raw


class ReplayController:
    """Owns replay state so the GUI can seek and scrub arbitrarily."""

    def __init__(self, path: str, speed: float = 1.0):
        self.path = path
        self.speed = speed
        self._playhead = 0.0
        self._paused = speed <= 0
        self.packets: list[ParsedDataPacket] = []
        for idx, raw in enumerate(read_dat_file(path)):
            payload = _normalize_replay_payload(raw)
            self.packets.append(parse_data_packet(payload, idx))
        self.buffers = SensorBuffers()
        self.current_index = 0
        if speed <= 0 and self.packets:
            self.seek(len(self.packets))

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
        new_buffers = SensorBuffers()
        for idx in range(target_index):
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
        self.replay_controller: Optional[ReplayController] = None
        self.replay_slider = None
        self.replay_button = None
        self.replay_status_text = None
        self._ignore_slider_change = False
        self._build_figure()

    def _build_figure(self):
        self.fig = plt.figure(
            figsize=(16, 13),
            facecolor="#1a1a2e",
        )
        self.fig.canvas.manager.set_window_title("WatchPAT ONE Dashboard")

        # Layout: 7 rows x 5 cols
        # Left (cols 0-2, rows 0-5): OxiA, OxiB, PAT, Chest, Heart Rate, SpO2
        # Right (cols 3-4, rows 0-5): HR readout, SpO2 readout, Body Pos, Accel, Session
        # Full width (row 6): Apnea Events Timeline
        gs = GridSpec(
            7, 5, figure=self.fig,
            left=0.06, right=0.98, top=0.94, bottom=0.04,
            hspace=0.45, wspace=0.4,
        )

        dark_bg = "#16213e"
        grid_color = "#2a2a4a"
        text_color = "#e0e0e0"

        # -- Waveform axes (left, rows 0-3) --
        self.wave_axes = {}
        self.wave_lines = {}
        channels = [
            ("OxiA", "Oximetry A (Red/IR)", 0),
            ("OxiB", "Oximetry B (Red/IR)", 1),
            ("PAT",  "PAT Signal", 2),
            ("Chest", "Chest / Resp. Effort", 3),
        ]
        for name, title, row in channels:
            ax = self.fig.add_subplot(gs[row, :3])
            ax.set_facecolor(dark_bg)
            ax.set_title(title, color=text_color, fontsize=9,
                         fontweight="bold", loc="left", pad=4)
            ax.tick_params(colors=text_color, labelsize=7)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            for spine in ax.spines.values():
                spine.set_color(grid_color)
            ax.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
            ax.set_xlim(0, BUFFER_SIZE)
            ax.set_ylabel("", fontsize=7, color=text_color)
            ax.set_xticklabels([])
            line, = ax.plot([], [], color=CHANNEL_COLORS[name],
                            linewidth=0.8, antialiased=True)
            self.wave_axes[name] = ax
            self.wave_lines[name] = line

        # -- Heart Rate trend (row 4 left) --
        self.ax_hr_trend = self.fig.add_subplot(gs[4, :3])
        self.ax_hr_trend.set_facecolor(dark_bg)
        self.ax_hr_trend.set_title("Heart Rate (BPM)", color=text_color,
                                    fontsize=9, fontweight="bold",
                                    loc="left", pad=4)
        self.ax_hr_trend.tick_params(colors=text_color, labelsize=7)
        self.ax_hr_trend.spines["top"].set_visible(False)
        self.ax_hr_trend.spines["right"].set_visible(False)
        for spine in self.ax_hr_trend.spines.values():
            spine.set_color(grid_color)
        self.ax_hr_trend.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
        self.ax_hr_trend.set_xlim(0, DERIVED_HISTORY)
        self.ax_hr_trend.set_ylim(40, 120)
        self.ax_hr_trend.set_xticklabels([])
        self.hr_trend_line, = self.ax_hr_trend.plot(
            [], [], color="#e74c3c", linewidth=1.5)

        # -- SpO2 trend (row 5 left) --
        self.ax_spo2_trend = self.fig.add_subplot(gs[5, :3])
        self.ax_spo2_trend.set_facecolor(dark_bg)
        self.ax_spo2_trend.set_title("SpO2 (%)", color=text_color,
                                      fontsize=9, fontweight="bold",
                                      loc="left", pad=4)
        self.ax_spo2_trend.tick_params(colors=text_color, labelsize=7)
        self.ax_spo2_trend.spines["top"].set_visible(False)
        self.ax_spo2_trend.spines["right"].set_visible(False)
        for spine in self.ax_spo2_trend.spines.values():
            spine.set_color(grid_color)
        self.ax_spo2_trend.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
        self.ax_spo2_trend.set_xlim(0, DERIVED_HISTORY)
        self.ax_spo2_trend.set_ylim(85, 100)
        self.ax_spo2_trend.set_xlabel("Seconds ago", fontsize=7,
                                       color=text_color)
        self.spo2_trend_line, = self.ax_spo2_trend.plot(
            [], [], color="#3498db", linewidth=1.5)

        # -- Right panel --

        # Heart Rate readout (row 0 right)
        self.ax_hr = self.fig.add_subplot(gs[0, 3:])
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

        # SpO2 readout (row 1 right)
        self.ax_spo2 = self.fig.add_subplot(gs[1, 3:])
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

        # Body position (row 2 right)
        self.ax_pos = self.fig.add_subplot(gs[2, 3:])
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

        # Accelerometer (row 3 right)
        self.ax_accel = self.fig.add_subplot(gs[3, 3:])
        self.ax_accel.set_facecolor(dark_bg)
        self.ax_accel.set_title("Accelerometer", color=text_color,
                                fontsize=9, fontweight="bold",
                                loc="left", pad=4)
        self.ax_accel.tick_params(colors=text_color, labelsize=7)
        for spine in self.ax_accel.spines.values():
            spine.set_color(grid_color)
        self.ax_accel.spines["top"].set_visible(False)
        self.ax_accel.spines["right"].set_visible(False)
        self.ax_accel.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
        self.ax_accel.set_xlim(0, MOTION_BUFFER)
        self.accel_lines = {
            "x": self.ax_accel.plot([], [], color="#e74c3c", linewidth=1,
                                     label="X")[0],
            "y": self.ax_accel.plot([], [], color="#2ecc71", linewidth=1,
                                     label="Y")[0],
            "z": self.ax_accel.plot([], [], color="#3498db", linewidth=1,
                                     label="Z")[0],
        }
        self.ax_accel.legend(fontsize=7, loc="upper right",
                             facecolor=dark_bg, edgecolor=grid_color,
                             labelcolor=text_color)

        # Session stats (rows 4-5 right)
        self.ax_stats = self.fig.add_subplot(gs[4:6, 3:])
        self.ax_stats.set_facecolor(dark_bg)
        self.ax_stats.set_xlim(0, 1)
        self.ax_stats.set_ylim(0, 1)
        self.ax_stats.axis("off")
        self.ax_stats.set_title("Session", color=text_color,
                                fontsize=9, fontweight="bold", pad=4)
        self.stats_text = self.ax_stats.text(
            0.05, 0.90, "", fontsize=9, color=text_color,
            fontfamily="monospace", verticalalignment="top",
            transform=self.ax_stats.transAxes)

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

        # -- Apnea / PAT Events Timeline (row 6, full width) --
        self.ax_apnea = self.fig.add_subplot(gs[6, :])
        self.ax_apnea.set_facecolor(dark_bg)
        self.ax_apnea.set_title(
            "Apnea Events  —  orange: PAT attenuation ≥30%  |  red: SpO₂ drop ≥3%",
            color=text_color, fontsize=9, fontweight="bold", loc="left", pad=4)
        self.ax_apnea.tick_params(colors=text_color, labelsize=7)
        self.ax_apnea.spines["top"].set_visible(False)
        for spine in self.ax_apnea.spines.values():
            spine.set_color(grid_color)
        self.ax_apnea.grid(True, color=grid_color, alpha=0.3, linewidth=0.5)
        self.ax_apnea.set_ylim(80, 102)
        self.ax_apnea.set_xlabel("Elapsed (min)", fontsize=7, color=text_color)
        self.ax_apnea.set_ylabel("SpO₂ (%)", fontsize=7, color="#3498db")
        self.ax_apnea.tick_params(axis="y", colors="#3498db")
        self.ax_apnea.set_title(
            "Apnea Events  —  "
            "■ Apnea  ■ Hypopnea  ■ RERA  ■ Central (no PAT)",
            color=text_color, fontsize=9, fontweight="bold", loc="left", pad=4)
        self.spo2_full_line, = self.ax_apnea.plot(
            [], [], color="#3498db", linewidth=1.0)
        # Colored legend squares drawn as text spans
        _legend_x = [0.01, 0.09, 0.19, 0.25]
        for xpos, color in zip(_legend_x,
                                [EVENT_COLORS[EVT_APNEA],
                                 EVENT_COLORS[EVT_HYPOPNEA],
                                 EVENT_COLORS[EVT_RERA],
                                 EVENT_COLORS[EVT_CENTRAL]]):
            self.ax_apnea.text(xpos, 0.97, "■", ha="left", va="top",
                               color=color, fontsize=9,
                               transform=self.ax_apnea.transAxes)
        # Per-type event line tracking (incremental, never removed)
        self._evt_lines: dict[str, list] = {t: [] for t in EVENT_COLORS}
        self._drawn_evt_count: dict[str, int] = {t: 0 for t in EVENT_COLORS}

        # Secondary y-axis: PAT amplitude as % of rolling baseline
        self.ax_pat_amp = self.ax_apnea.twinx()
        self.ax_pat_amp.set_facecolor("none")
        self.ax_pat_amp.set_ylim(0, 150)
        self.ax_pat_amp.set_ylabel("PAT amp (% baseline)", fontsize=7,
                                    color="#2ecc71")
        self.ax_pat_amp.tick_params(axis="y", colors="#2ecc71", labelsize=7)
        self.ax_pat_amp.spines["right"].set_color("#2ecc71")
        self.ax_pat_amp.spines["top"].set_visible(False)
        self.ax_pat_amp.spines["left"].set_visible(False)
        self.ax_pat_amp.axhline(y=70, color="#f39c12", linewidth=0.8,
                                 linestyle="--", alpha=0.7)
        self.pat_amp_line, = self.ax_pat_amp.plot(
            [], [], color="#2ecc71", linewidth=0.8, alpha=0.85)

        self.ax_apnea_ahi_text = self.ax_apnea.text(
            0.99, 0.93, "pAHI: --",
            ha="right", va="top", fontsize=9, fontweight="bold",
            color="#7f8c8d", transform=self.ax_apnea.transAxes)

        self.fig.canvas.mpl_connect("close_event", self._on_close)
        manager = getattr(self.fig.canvas, "manager", None)
        window = getattr(manager, "window", None)
        protocol = getattr(window, "protocol", None)
        if callable(protocol):
            protocol("WM_DELETE_WINDOW", self.request_close)

    def enable_replay_scrubber(self, controller: ReplayController):
        """Attach replay controls for seeking through preloaded data."""
        self.replay_controller = controller
        self.buffers = controller.buffers
        self.fig.subplots_adjust(bottom=0.10)

        dark_bg = "#16213e"
        text_color = "#e0e0e0"
        slider_ax = self.fig.add_axes([0.10, 0.02, 0.70, 0.03], facecolor=dark_bg)
        button_ax = self.fig.add_axes([0.82, 0.018, 0.08, 0.04])
        status_ax = self.fig.add_axes([0.91, 0.016, 0.07, 0.05], facecolor="none")
        status_ax.axis("off")

        max_packet = max(controller.packet_count, 1)
        self.replay_slider = Slider(
            slider_ax,
            "Replay",
            0,
            max_packet,
            valinit=controller.current_index,
            valstep=1,
            color="#3498db",
            dragging=False,
        )
        self.replay_slider.label.set_color(text_color)
        self.replay_slider.valtext.set_color(text_color)
        self.replay_button = Button(
            button_ax,
            "Pause" if not controller.paused else "Play",
            color="#2a2a4a",
            hovercolor="#3a3a5a",
        )
        self.replay_status_text = status_ax.text(
            0.5, 0.5, "",
            ha="center", va="center", fontsize=9, color=text_color,
            transform=status_ax.transAxes,
        )

        def _on_slider_change(val):
            if self._ignore_slider_change:
                return
            controller.seek(int(val))
            self.buffers = controller.buffers
            if self.fig.canvas:
                self.fig.canvas.draw_idle()

        def _on_button(_event):
            paused = controller.toggle_paused()
            self.replay_button.label.set_text("Play" if paused else "Pause")
            if self.fig.canvas:
                self.fig.canvas.draw_idle()

        self.replay_slider.on_changed(_on_slider_change)
        self.replay_button.on_clicked(_on_button)
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
            self.replay_button.label.set_text("Play" if ctrl.paused else "Pause")
        if self.replay_status_text is not None:
            total = max(ctrl.packet_count, 1)
            cur_min = ctrl.current_index / 60.0
            total_min = ctrl.packet_count / 60.0
            self.replay_status_text.set_text(
                f"{cur_min:.1f}/{total_min:.1f}m\n{ctrl.current_index}/{total}"
            )

    def _reset_event_lines(self):
        for lines in self._evt_lines.values():
            for line in lines:
                line.remove()
            lines.clear()
        for evt_type in self._drawn_evt_count:
            self._drawn_evt_count[evt_type] = 0

    def update(self, frame):
        """Animation update callback."""
        if self.replay_controller is not None:
            packets_per_second = (self.replay_controller.speed
                                  if self.replay_controller.speed > 0 else 0.0)
            self.replay_controller.advance(packets_per_second / FPS)
            self.buffers = self.replay_controller.buffers
            self._sync_replay_controls()
        snap = self.buffers.snapshot()

        # -- Update waveforms --
        for name, key in [("OxiA", "oxi_a"), ("OxiB", "oxi_b"),
                          ("PAT", "pat"), ("Chest", "chest")]:
            data = snap[key]
            line = self.wave_lines[name]
            ax = self.wave_axes[name]
            if len(data) > 0:
                x = np.arange(len(data))
                line.set_data(x, data)
                ax.set_xlim(0, max(len(data), BUFFER_SIZE))
                lo, hi = np.min(data), np.max(data)
                margin = max((hi - lo) * 0.1, 1)
                ax.set_ylim(lo - margin, hi + margin)
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
            x = np.arange(len(spo2_data))
            self.spo2_trend_line.set_data(x, spo2_data)
            self.ax_spo2_trend.set_xlim(0, max(len(spo2_data), DERIVED_HISTORY))
            valid = spo2_data[~np.isnan(spo2_data)]
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
        spo2_times = snap["spo2_full_times"]
        spo2_full = snap["spo2_full_history"]
        t_max_min = 1.0
        if len(spo2_times) > 0:
            t_min = spo2_times / 60.0
            t_max_min = max(float(t_min[-1]), 1.0)
            self.spo2_full_line.set_data(t_min, spo2_full)
            self.ax_apnea.set_xlim(0, t_max_min)
            valid_spo2 = spo2_full[~np.isnan(spo2_full)]
            if len(valid_spo2) > 0:
                lo = max(60.0, float(np.min(valid_spo2)) - 2)
                self.ax_apnea.set_ylim(lo, 102)
        else:
            self.spo2_full_line.set_data([], [])

        # PAT amplitude trace
        pat_times = snap["pat_amp_times"]
        pat_amps = snap["pat_amp_history"]
        if len(pat_times) > 0:
            self.pat_amp_line.set_data(pat_times / 60.0, pat_amps)
            self.ax_pat_amp.set_xlim(0, t_max_min)
        else:
            self.pat_amp_line.set_data([], [])

        self._reset_event_lines()

        # PAT events: draw current markers per type
        pat_events = snap["pat_events"]
        for ev_time, _, _, _, evt_type in pat_events:
            color = EVENT_COLORS.get(evt_type, "#95a5a6")
            vl = self.ax_apnea.axvline(
                x=ev_time / 60.0, color=color, alpha=0.75, linewidth=1.5)
            self._evt_lines[evt_type].append(vl)
            self._drawn_evt_count[evt_type] += 1

        # Central apnea markers (purple)
        central_events = snap["central_events"]
        for ev_time, _ in central_events:
            vl = self.ax_apnea.axvline(
                x=ev_time / 60.0, color=EVENT_COLORS[EVT_CENTRAL],
                alpha=0.75, linewidth=1.5)
            self._evt_lines[EVT_CENTRAL].append(vl)
            self._drawn_evt_count[EVT_CENTRAL] += 1

        # AHI/RDI text
        pahi = snap["pahi_estimate"]
        rdi  = snap["rdi_estimate"]
        ahi  = snap["ahi_estimate"]
        n_central = len(central_events)
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
        n_central_stat = len(snap["central_events"])
        stats_lines = [
            f"Packets:   {snap['packet_count']:>8d}",
            f"Data:      {kb:>7.1f} KB",
            f"Rate:      {pkt_rate:>7.1f} p/s",
            f"Elapsed:   {mins:>4d}m {secs:02d}s",
            f"pAHI:      {pahi_str:>5s} /hr",
            f"pRDI:      {rdi_str:>5s} /hr",
            f"Central:   {n_central_stat:>8d}",
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
            self.pat_amp_line,
            self.ax_apnea_ahi_text,
        ])
        for lines in self._evt_lines.values():
            artists.extend(lines)
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
    replay_controller = None

    if args.replay:
        dashboard.set_mode_label(f"Replay: {args.replay}  ({args.speed}x)")
        replay_controller = ReplayController(args.replay, args.speed)
        dashboard.enable_replay_scrubber(replay_controller)
        dashboard.buffers = replay_controller.buffers
        t = None
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
