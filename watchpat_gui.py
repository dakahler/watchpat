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
import struct
import sys
import threading
import time
from collections import deque
from queue import Queue, Empty

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import FancyBboxPatch, Circle
from matplotlib.gridspec import GridSpec
import numpy as np

from watchpat_ble import (
    WatchPATClient, ParsedDataPacket, DecodedWaveform,
    MotionRecord, MetricRecord, RecordKind,
    read_dat_file, parse_data_packet, format_parsed_packet,
)

logger = logging.getLogger("watchpat.gui")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WINDOW_SECONDS = 10        # Rolling window width
WAVEFORM_RATE = 100        # Nominal sample rate for waveform channels
MOTION_RATE = 5            # Subframes per second
BUFFER_SIZE = WINDOW_SECONDS * WAVEFORM_RATE  # 1000 samples
MOTION_BUFFER = WINDOW_SECONDS * MOTION_RATE  # 50 samples
FPS = 15                   # GUI refresh rate
DERIVED_HISTORY = 120      # Seconds of HR/SpO2 trend to display
DERIVE_INTERVAL = 1.0      # Seconds between derived signal updates

CHANNEL_COLORS = {
    "OxiA":  "#e74c3c",    # Red
    "OxiB":  "#3498db",    # Blue
    "PAT":   "#2ecc71",    # Green
    "Chest": "#f39c12",    # Orange
}

POSITION_LABELS = {
    "z+": "Supine",
    "z-": "Prone",
    "y+": "Left",
    "y-": "Right",
    "x+": "Upright",
    "x-": "Inverted",
}


# ---------------------------------------------------------------------------
# Real-time derived signal computation (adapted from watchpat_to_resmed_sd)
# ---------------------------------------------------------------------------

def _detect_peaks_rt(samples: np.ndarray, rate: int) -> list[int]:
    """Detect pulse peaks in a waveform buffer. Returns peak indices."""
    if len(samples) < rate * 3:
        return []
    # Baseline removal via moving average (~0.75 Hz window)
    win = max(3, int(rate * 0.75))
    kernel = np.ones(win) / win
    baseline = np.convolve(samples, kernel, mode="same")
    detrended = samples - baseline

    abs_median = float(np.median(np.abs(detrended)))
    threshold = max(20.0, abs_median * 1.5)
    refractory = max(1, int(rate * 0.35))
    prom_win = max(3, int(rate * 0.15))

    peaks: list[int] = []
    last_peak = -refractory
    for i in range(1, len(detrended) - 1):
        if i - last_peak < refractory:
            continue
        c = detrended[i]
        if c <= threshold:
            continue
        if c <= detrended[i - 1] or c < detrended[i + 1]:
            continue
        lo = max(0, i - prom_win)
        hi = min(len(detrended), i + prom_win + 1)
        local_min = float(np.min(detrended[lo:hi]))
        if (c - local_min) < threshold * 1.2:
            continue
        peaks.append(i)
        last_peak = i
    return peaks


def _compute_heart_rate(samples: np.ndarray, rate: int) -> float:
    """Compute heart rate (BPM) from a waveform buffer. Returns -1 on failure."""
    peaks = _detect_peaks_rt(samples, rate)
    if len(peaks) < 3:
        return -1.0
    # Compute BPM from plausible inter-peak intervals
    bpms = []
    min_ivl = rate * 60.0 / 140.0
    max_ivl = rate * 60.0 / 40.0
    for a, b in zip(peaks, peaks[1:]):
        delta = b - a
        if min_ivl <= delta <= max_ivl:
            bpms.append(60.0 * rate / delta)
    if len(bpms) < 2:
        return -1.0
    return float(np.median(bpms))


def _sinusoid_amplitude(samples: np.ndarray, hz: float, rate: int) -> float:
    """Amplitude of a sinusoid at frequency hz via single-bin DFT."""
    if len(samples) == 0 or hz <= 0:
        return 0.0
    mean_val = float(np.mean(samples))
    n = np.arange(len(samples))
    angle = 2.0 * math.pi * hz * n / rate
    centered = samples - mean_val
    real_part = float(np.sum(centered * np.cos(angle)))
    imag_part = float(-np.sum(centered * np.sin(angle)))
    return (2.0 / len(samples)) * math.sqrt(real_part**2 + imag_part**2)


def _compute_spo2_pair(red: np.ndarray, ir: np.ndarray, bpm: float,
                       rate: int) -> tuple[float, float]:
    """Compute SpO2 from a specific red/IR assignment using a 4-second window.
    Returns (spo2, ratio) or (-1, -1) on failure."""
    # Use a 4-second window from the tail, matching the batch converter
    n = min(len(red), len(ir), rate * 4)
    if n < rate * 2:
        return -1.0, -1.0
    red_win = red[-n:]
    ir_win = ir[-n:]

    red_dc = float(np.mean(red_win))
    ir_dc = float(np.mean(ir_win))
    # Reject when DC is near zero (delta-encoded baseline crossing zero)
    if abs(red_dc) < 10.0 or abs(ir_dc) < 10.0:
        return -1.0, -1.0

    pulse_hz = bpm / 60.0
    red_ac = _sinusoid_amplitude(red_win, pulse_hz, rate)
    ir_ac = _sinusoid_amplitude(ir_win, pulse_hz, rate)
    if red_ac <= 0 or ir_ac <= 0:
        return -1.0, -1.0

    # AC/DC modulation depth — reject if AC is implausibly large vs DC
    if red_ac / abs(red_dc) > 0.5 or ir_ac / abs(ir_dc) > 0.5:
        return -1.0, -1.0

    ratio = abs((red_ac / red_dc) / (ir_ac / ir_dc))
    spo2 = 116.0 - 25.0 * ratio
    if 60.0 <= spo2 <= 100.0:
        return spo2, ratio
    return -1.0, ratio


# ---------------------------------------------------------------------------
# Data buffer — thread-safe accumulator for incoming parsed packets
# ---------------------------------------------------------------------------
class SensorBuffers:
    """Thread-safe rolling buffers for all sensor channels."""

    def __init__(self):
        self.lock = threading.Lock()
        # Waveform rolling buffers (values)
        self.oxi_a = deque(maxlen=BUFFER_SIZE)
        self.oxi_b = deque(maxlen=BUFFER_SIZE)
        self.pat = deque(maxlen=BUFFER_SIZE)
        self.chest = deque(maxlen=BUFFER_SIZE)

        # Motion rolling buffers
        self.accel_x = deque(maxlen=MOTION_BUFFER)
        self.accel_y = deque(maxlen=MOTION_BUFFER)
        self.accel_z = deque(maxlen=MOTION_BUFFER)
        self.field_a = deque(maxlen=MOTION_BUFFER)
        self.field_b = deque(maxlen=MOTION_BUFFER)

        # Derived signals (1 Hz history)
        self.hr_history = deque(maxlen=DERIVED_HISTORY)
        self.spo2_history = deque(maxlen=DERIVED_HISTORY)
        self.current_hr = -1.0
        self.current_spo2 = -1.0
        self._last_derive_time = 0.0
        # SpO2 assignment tracking: decaying scores (recent performance)
        self._spo2_score_ab = 0.0   # OxiA=red, OxiB=IR
        self._spo2_score_ba = 0.0   # OxiB=red, OxiA=IR
        # Raw SpO2 values before smoothing
        self._spo2_raw = deque(maxlen=15)
        self._spo2_ema = -1.0  # exponential moving average

        # Scalar state
        self.body_position = "?"
        self.body_position_label = "Unknown"
        self.metric_value = 0
        self.packet_count = 0
        self.total_bytes = 0
        self.start_time = None
        self.last_motion_crc_ok = 0
        self.last_motion_crc_total = 0
        self.events = deque(maxlen=20)

    def feed(self, pkt: ParsedDataPacket):
        """Ingest a parsed data packet into the rolling buffers."""
        with self.lock:
            if self.start_time is None:
                self.start_time = time.time()
            self.packet_count += 1
            self.total_bytes += len(pkt.raw_payload)

            for wf in pkt.waveforms:
                buf = self._waveform_buf(wf.channel_name)
                if buf is not None:
                    buf.extend(wf.samples)

            if pkt.motion is not None:
                m = pkt.motion
                for sf in m.subframes:
                    self.accel_x.append(sf.x)
                    self.accel_y.append(sf.y)
                    self.accel_z.append(sf.z)
                    self.field_a.append(sf.field_a)
                    self.field_b.append(sf.field_b)
                self.body_position = m.body_position
                self.body_position_label = POSITION_LABELS.get(
                    m.body_position, m.body_position)
                ok = sum(1 for sf in m.subframes if sf.crc_valid)
                self.last_motion_crc_ok = ok
                self.last_motion_crc_total = len(m.subframes)

            if pkt.metric is not None:
                self.metric_value = pkt.metric.value

            for ev in pkt.events:
                self.events.append(
                    f"#{self.packet_count}: {ev.kind.name} val={ev.value}")

            # Compute derived HR/SpO2 at ~1 Hz
            now = time.time()
            if now - self._last_derive_time >= DERIVE_INTERVAL:
                self._last_derive_time = now
                self._update_derived()

    def _update_derived(self):
        """Compute heart rate and SpO2 from current raw buffers (called with lock held)."""
        # Try PAT first, then OxiB, then OxiA for heart rate
        hr = -1.0
        for buf in (self.pat, self.oxi_b, self.oxi_a):
            if len(buf) >= WAVEFORM_RATE * 5:
                hr = _compute_heart_rate(np.array(buf), WAVEFORM_RATE)
                if hr > 0:
                    break
        self.current_hr = hr
        self.hr_history.append(hr if hr > 0 else float("nan"))

        # SpO2: 4s window, decaying assignment scores, median-smooth output
        spo2 = -1.0
        min_samples = WAVEFORM_RATE * 4
        if hr > 0 and len(self.oxi_a) >= min_samples and len(self.oxi_b) >= min_samples:
            a_arr = np.array(self.oxi_a)
            b_arr = np.array(self.oxi_b)

            # Quality gate: reject when channels are nearly identical
            # (device sending duplicate data = no real Red/IR separation)
            n_check = min(len(a_arr), len(b_arr), WAVEFORM_RATE * 4)
            a_tail = a_arr[-n_check:]
            b_tail = b_arr[-n_check:]
            dc_a, dc_b = float(np.mean(a_tail)), float(np.mean(b_tail))
            if abs(dc_a) > 10 and abs(dc_b) > 10:
                dc_ratio = dc_a / dc_b
            else:
                dc_ratio = 1.0  # can't determine → treat as invalid
            # Channels are distinct when DC ratio is far from 1.0
            # (real Red/IR typically have very different baselines)
            channels_distinct = abs(dc_ratio - 1.0) > 0.08

            if channels_distinct:
                # Compute both assignments
                spo2_ab, ratio_ab = _compute_spo2_pair(
                    a_arr, b_arr, hr, WAVEFORM_RATE)
                spo2_ba, ratio_ba = _compute_spo2_pair(
                    b_arr, a_arr, hr, WAVEFORM_RATE)
                # Decay old scores, then reward plausible ratios
                self._spo2_score_ab *= 0.9
                self._spo2_score_ba *= 0.9
                if spo2_ab > 0 and ratio_ab > 0 and 0.4 <= ratio_ab <= 1.3:
                    self._spo2_score_ab += 1.0 - abs(ratio_ab - 0.7)
                if spo2_ba > 0 and ratio_ba > 0 and 0.4 <= ratio_ba <= 1.3:
                    self._spo2_score_ba += 1.0 - abs(ratio_ba - 0.7)
                # Pick assignment with better recent score
                if self._spo2_score_ab >= self._spo2_score_ba:
                    spo2 = spo2_ab if spo2_ab > 0 else spo2_ba
                else:
                    spo2 = spo2_ba if spo2_ba > 0 else spo2_ab

        # Heavy smoothing: median filter + outlier rejection + EMA
        self._spo2_raw.append(spo2)
        valid_raw = sorted([v for v in self._spo2_raw if v > 0])
        if valid_raw:
            # Trimmed median: drop top/bottom 20% before taking median
            trim = max(1, len(valid_raw) // 5)
            trimmed = valid_raw[trim:-trim] if len(valid_raw) > 4 else valid_raw
            med = trimmed[len(trimmed) // 2]
            # EMA for further smoothing (alpha=0.15 → ~7s time constant)
            if self._spo2_ema < 0:
                self._spo2_ema = med
            else:
                self._spo2_ema += 0.15 * (med - self._spo2_ema)
            self.current_spo2 = self._spo2_ema
            self.spo2_history.append(self._spo2_ema)
        else:
            self.current_spo2 = -1.0
            self.spo2_history.append(float("nan"))

    def _waveform_buf(self, name: str):
        return {"OxiA": self.oxi_a, "OxiB": self.oxi_b,
                "PAT": self.pat, "Chest": self.chest}.get(name)

    def snapshot(self):
        """Return a consistent snapshot of all buffers for rendering."""
        with self.lock:
            return {
                "oxi_a": np.array(self.oxi_a) if self.oxi_a else np.array([]),
                "oxi_b": np.array(self.oxi_b) if self.oxi_b else np.array([]),
                "pat": np.array(self.pat) if self.pat else np.array([]),
                "chest": np.array(self.chest) if self.chest else np.array([]),
                "accel_x": np.array(self.accel_x) if self.accel_x else np.array([]),
                "accel_y": np.array(self.accel_y) if self.accel_y else np.array([]),
                "accel_z": np.array(self.accel_z) if self.accel_z else np.array([]),
                "field_a": np.array(self.field_a) if self.field_a else np.array([]),
                "field_b": np.array(self.field_b) if self.field_b else np.array([]),
                "hr_history": np.array(self.hr_history) if self.hr_history else np.array([]),
                "spo2_history": np.array(self.spo2_history) if self.spo2_history else np.array([]),
                "current_hr": self.current_hr,
                "current_spo2": self.current_spo2,
                "body_position": self.body_position,
                "body_position_label": self.body_position_label,
                "metric": self.metric_value,
                "packet_count": self.packet_count,
                "total_bytes": self.total_bytes,
                "start_time": self.start_time,
                "motion_crc": (self.last_motion_crc_ok, self.last_motion_crc_total),
                "events": list(self.events),
            }


# ---------------------------------------------------------------------------
# Dashboard figure
# ---------------------------------------------------------------------------
class WatchPATDashboard:
    """Matplotlib-based real-time dashboard."""

    def __init__(self, buffers: SensorBuffers):
        self.buffers = buffers
        self._build_figure()

    def _build_figure(self):
        self.fig = plt.figure(
            figsize=(16, 11),
            facecolor="#1a1a2e",
        )
        self.fig.canvas.manager.set_window_title("WatchPAT ONE Dashboard")

        # Layout: 6 rows x 5 cols
        # Left (cols 0-2): OxiA, OxiB, PAT, Chest, Heart Rate, SpO2
        # Right (cols 3-4): HR readout, SpO2 readout, Body Pos, Accel, Session
        gs = GridSpec(
            6, 5, figure=self.fig,
            left=0.06, right=0.98, top=0.94, bottom=0.04,
            hspace=0.40, wspace=0.4,
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

    def update(self, frame):
        """Animation update callback."""
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
        stats_lines = [
            f"Packets:   {snap['packet_count']:>8d}",
            f"Data:      {kb:>7.1f} KB",
            f"Rate:      {pkt_rate:>7.1f} p/s",
            f"Elapsed:   {mins:>4d}m {secs:02d}s",
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
        ])
        return artists

    def set_mode_label(self, text: str):
        self.mode_text.set_text(text)

    def run(self):
        """Start the animation loop (blocks until window closes)."""
        self.anim = FuncAnimation(
            self.fig, self.update, interval=1000 // FPS,
            blit=False, cache_frame_data=False,
        )
        plt.show()


# ---------------------------------------------------------------------------
# Replay feeder — reads a .dat file and feeds packets at paced rate
# ---------------------------------------------------------------------------
def replay_feeder(path: str, buffers: SensorBuffers, speed: float = 1.0,
                  stop_event: threading.Event = None):
    """Feed packets from a .dat file into buffers at real-time pace."""
    for idx, raw in enumerate(read_dat_file(path)):
        if stop_event and stop_event.is_set():
            break
        pkt = parse_data_packet(raw, idx)
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
        devices = await wp.scan(timeout=scan_time, serial_filter=serial)
        if not devices:
            logger.error("No WatchPAT devices found.")
            return

        device = devices[0]
        logger.info("Connecting to %s ...", device.name)
        await wp.connect(device)

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

    if args.replay:
        dashboard.set_mode_label(f"Replay: {args.replay}  ({args.speed}x)")
        t = threading.Thread(
            target=replay_feeder,
            args=(args.replay, buffers, args.speed, stop_event),
            daemon=True,
        )
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

    t.start()

    try:
        dashboard.run()  # Blocks until window closed
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        t.join(timeout=5)
        snap = buffers.snapshot()
        print(f"\nSession summary: {snap['packet_count']} packets, "
              f"{snap['total_bytes']/1024:.1f} KB")


if __name__ == "__main__":
    main()
