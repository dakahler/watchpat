"""
WatchPAT ONE Multi-Night Trends
================================
Visualize sleep metrics across multiple nights from a folder of .dat files.

Usage:
    python watchpat_trends.py /path/to/dat/folder
    python watchpat_trends.py            # prompts for folder via dialog
"""

import argparse
import os
import re
import sys
import struct
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
import numpy as np


def _configure_backend():
    if os.environ.get("MPLBACKEND"):
        return
    preferred = ["MacOSX", "TkAgg"] if sys.platform == "darwin" else ["TkAgg"]
    for backend in preferred:
        try:
            matplotlib.use(backend)
            return
        except Exception:
            pass


_configure_backend()
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

from watchpat_ble import (
    HEADER_SIZE,
    read_dat_file,
    parse_data_packet,
)
from watchpat_analysis import (
    SensorBuffers,
    SLEEP_STAGE_AWAKE, SLEEP_STAGE_LIGHT, SLEEP_STAGE_DEEP, SLEEP_STAGE_REM,
    EVT_APNEA, EVT_HYPOPNEA, EVT_RERA, EVT_PAT, EVT_CENTRAL,
)

# ---------------------------------------------------------------------------
# Colors — match the dark theme used in watchpat_gui.py
# ---------------------------------------------------------------------------
BG_DARK   = "#1a1a2e"
BG_MID    = "#16213e"
BG_PANEL  = "#0f0f23"
FG        = "#ecf0f1"
GRID_CLR  = "#2c3e50"

SLEEP_STAGE_COLORS = {
    SLEEP_STAGE_AWAKE: "#ffd84d",
    SLEEP_STAGE_LIGHT: "#5cc8ff",
    SLEEP_STAGE_DEEP:  "#2ee6a6",
    SLEEP_STAGE_REM:   "#ff73c6",
}
AHI_COLOR  = "#e74c3c"
PAHI_COLOR = "#e67e22"
RDI_COLOR  = "#f1c40f"
HR_AVG_COLOR  = "#e74c3c"
HR_MAX_COLOR  = "#c0392b"
SPO2_AVG_COLOR = "#3498db"
SPO2_MIN_COLOR = "#1a5276"
MOTION_COLOR   = "#95a5a6"

EVT_COLORS = {
    EVT_APNEA:    "#e74c3c",
    EVT_HYPOPNEA: "#e67e22",
    EVT_RERA:     "#f1c40f",
    EVT_CENTRAL:  "#9b59b6",
    EVT_PAT:      "#95a5a6",
}

# ---------------------------------------------------------------------------
# Date extraction helpers
# ---------------------------------------------------------------------------
_DATE_PATTERNS = [
    re.compile(r'(\d{4})[_\-](\d{2})[_\-](\d{2})'),   # YYYY-MM-DD
    re.compile(r'(\d{4})(\d{2})(\d{2})'),               # YYYYMMDD
    re.compile(r'(\d{2})[_\-](\d{2})[_\-](\d{4})'),   # MM-DD-YYYY
]


def _date_from_filename(name: str) -> Optional[datetime]:
    stem = Path(name).stem
    for pat in _DATE_PATTERNS:
        m = pat.search(stem)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if y < 100:
                    y, mo, d = d, int(m.group(1)), int(m.group(2))
                return datetime(y, mo, d)
            except ValueError:
                continue
    return None


def _date_from_path(path: Path) -> datetime:
    dt = _date_from_filename(path.name)
    if dt:
        return dt
    return datetime.fromtimestamp(path.stat().st_mtime).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


# ---------------------------------------------------------------------------
# Payload normaliser (same logic as watchpat_gui._normalize_replay_payload)
# ---------------------------------------------------------------------------
def _norm(raw: bytes) -> bytes:
    if len(raw) >= HEADER_SIZE and raw[:2] == b"\xBB\xBB":
        return raw[HEADER_SIZE:]
    return raw


# ---------------------------------------------------------------------------
# Process a single .dat file → summary dict
# ---------------------------------------------------------------------------
def process_file(path: Path) -> Optional[dict]:
    buffers = SensorBuffers(full_session_history=True)
    try:
        for idx, raw in enumerate(read_dat_file(str(path))):
            pkt = parse_data_packet(_norm(raw), idx)
            buffers.feed(pkt, now=float(idx))
    except Exception as exc:
        print(f"  [warn] {path.name}: {exc}", file=sys.stderr)
        return None

    if buffers.packet_count == 0:
        return None

    hr_arr = np.asarray([v for v in buffers.hr_full_history if v > 0], dtype=float)
    spo2_arr = np.asarray([v for v in buffers.spo2_full_history if v > 0], dtype=float)
    motion_arr = np.asarray(list(buffers.motion_level_history), dtype=float)
    motion_arr = motion_arr[np.isfinite(motion_arr)]

    stage_pct = buffers.sleep_stage_percentages()

    pat_counts = buffers.pat_event_type_counts

    return {
        "path": path,
        "date": _date_from_path(path),
        "duration_hours": buffers.packet_count / 3600.0,
        "ahi":  buffers.ahi_estimate  if buffers.ahi_estimate  >= 0 else float("nan"),
        "pahi": buffers.pahi_estimate if buffers.pahi_estimate >= 0 else float("nan"),
        "rdi":  buffers.rdi_estimate  if buffers.rdi_estimate  >= 0 else float("nan"),
        "hr_mean": float(np.mean(hr_arr))   if hr_arr.size   else float("nan"),
        "hr_max":  float(np.max(hr_arr))    if hr_arr.size   else float("nan"),
        "spo2_mean": float(np.mean(spo2_arr)) if spo2_arr.size else float("nan"),
        "spo2_min":  float(np.min(spo2_arr))  if spo2_arr.size else float("nan"),
        "motion_mean": float(np.mean(motion_arr)) if motion_arr.size else float("nan"),
        "stage_awake": stage_pct.get(SLEEP_STAGE_AWAKE, 0.0),
        "stage_light": stage_pct.get(SLEEP_STAGE_LIGHT, 0.0),
        "stage_deep":  stage_pct.get(SLEEP_STAGE_DEEP,  0.0),
        "stage_rem":   stage_pct.get(SLEEP_STAGE_REM,   0.0),
        "n_apnea":    pat_counts.get(EVT_APNEA,    0),
        "n_hypopnea": pat_counts.get(EVT_HYPOPNEA, 0),
        "n_rera":     pat_counts.get(EVT_RERA,     0),
        "n_central":  buffers.central_event_count,
        "n_pat":      pat_counts.get(EVT_PAT,      0),
    }


# ---------------------------------------------------------------------------
# Load folder
# ---------------------------------------------------------------------------
def load_folder(folder: str) -> list[dict]:
    dat_files = sorted(Path(folder).glob("*.dat"), key=lambda p: _date_from_path(p))
    if not dat_files:
        print(f"No .dat files found in {folder}", file=sys.stderr)
        return []

    results = []
    for i, path in enumerate(dat_files, 1):
        print(f"[{i}/{len(dat_files)}] Processing {path.name} …", flush=True)
        rec = process_file(path)
        if rec:
            results.append(rec)
        else:
            print(f"  Skipped (no data).", file=sys.stderr)

    return results


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------
def _style_ax(ax, title: str, ylabel: str = ""):
    ax.set_facecolor(BG_MID)
    ax.tick_params(colors=FG, labelsize=8)
    ax.set_title(title, color=FG, fontsize=9, pad=4)
    if ylabel:
        ax.set_ylabel(ylabel, color=FG, fontsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(GRID_CLR)
    ax.yaxis.grid(True, color=GRID_CLR, linewidth=0.5, linestyle="--", alpha=0.6)
    ax.set_axisbelow(True)


def _set_date_ticks(ax, dates: list[datetime], records: list[dict]):
    labels = [d.strftime("%b %-d") for d in dates]
    ax.set_xticks(range(len(dates)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7, color=FG)


def _safe_line(ax, xs, ys, **kwargs):
    """Plot line, skipping NaN gaps."""
    ys = np.asarray(ys, dtype=float)
    valid = np.isfinite(ys)
    if not valid.any():
        return
    ax.plot(np.array(xs)[valid], ys[valid], **kwargs)


# ---------------------------------------------------------------------------
# Build the figure
# ---------------------------------------------------------------------------
def build_figure(records: list[dict]) -> plt.Figure:
    dates = [r["date"] for r in records]
    xs = list(range(len(records)))

    fig = plt.figure(figsize=(14, 11), facecolor=BG_DARK)
    fig.suptitle("WatchPAT ONE — Multi-Night Sleep Trends", color=FG,
                 fontsize=13, fontweight="bold", y=0.98)

    gs = GridSpec(4, 2, figure=fig, hspace=0.55, wspace=0.35,
                  left=0.07, right=0.97, top=0.93, bottom=0.08)

    # ------------------------------------------------------------------
    # Panel 1: AHI / pAHI / RDI
    # ------------------------------------------------------------------
    ax_ahi = fig.add_subplot(gs[0, 0])
    _style_ax(ax_ahi, "Respiratory Index", "/hr")
    _safe_line(ax_ahi, xs, [r["ahi"]  for r in records],
               color=AHI_COLOR,  marker="o", markersize=5, linewidth=1.5,
               label="AHI", zorder=3)
    _safe_line(ax_ahi, xs, [r["pahi"] for r in records],
               color=PAHI_COLOR, marker="s", markersize=5, linewidth=1.5,
               label="pAHI", zorder=3)
    _safe_line(ax_ahi, xs, [r["rdi"]  for r in records],
               color=RDI_COLOR,  marker="^", markersize=5, linewidth=1.5,
               label="RDI", zorder=3)
    ax_ahi.axhline(5,  color=AHI_COLOR,  linewidth=0.7, linestyle=":", alpha=0.5)
    ax_ahi.axhline(15, color=PAHI_COLOR, linewidth=0.7, linestyle=":", alpha=0.5)
    ax_ahi.axhline(30, color=RDI_COLOR,  linewidth=0.7, linestyle=":", alpha=0.5)
    ax_ahi.legend(fontsize=7, facecolor=BG_PANEL, labelcolor=FG, framealpha=0.7,
                  loc="upper left")
    _set_date_ticks(ax_ahi, dates, records)
    ax_ahi.set_xlim(-0.5, len(xs) - 0.5)

    # ------------------------------------------------------------------
    # Panel 2: Sleep stage percentages (stacked bar)
    # ------------------------------------------------------------------
    ax_stages = fig.add_subplot(gs[0, 1])
    _style_ax(ax_stages, "Sleep Stages", "%")
    bar_w = 0.65
    bottoms = np.zeros(len(records))
    for stage, color in [
        (SLEEP_STAGE_DEEP,  SLEEP_STAGE_COLORS[SLEEP_STAGE_DEEP]),
        (SLEEP_STAGE_REM,   SLEEP_STAGE_COLORS[SLEEP_STAGE_REM]),
        (SLEEP_STAGE_LIGHT, SLEEP_STAGE_COLORS[SLEEP_STAGE_LIGHT]),
        (SLEEP_STAGE_AWAKE, SLEEP_STAGE_COLORS[SLEEP_STAGE_AWAKE]),
    ]:
        key = f"stage_{stage.lower()}"
        vals = np.asarray([r[key] for r in records], dtype=float)
        ax_stages.bar(xs, vals, bar_w, bottom=bottoms, color=color, label=stage)
        bottoms += vals
    ax_stages.set_ylim(0, 105)
    ax_stages.legend(fontsize=7, facecolor=BG_PANEL, labelcolor=FG, framealpha=0.7,
                     loc="upper right")
    _set_date_ticks(ax_stages, dates, records)
    ax_stages.set_xlim(-0.5, len(xs) - 0.5)

    # ------------------------------------------------------------------
    # Panel 3: Time asleep
    # ------------------------------------------------------------------
    ax_time = fig.add_subplot(gs[1, 0])
    _style_ax(ax_time, "Recording Duration", "hours")
    durations = [r["duration_hours"] for r in records]
    ax_time.bar(xs, durations, bar_w, color="#5cc8ff", alpha=0.85)
    ax_time.axhline(7, color=FG, linewidth=0.8, linestyle="--", alpha=0.4)
    ax_time.axhline(8, color=FG, linewidth=0.8, linestyle="--", alpha=0.4)
    _set_date_ticks(ax_time, dates, records)
    ax_time.set_xlim(-0.5, len(xs) - 0.5)
    ax_time.set_ylim(0, max(durations + [8.5]) * 1.1 if durations else 10)

    # ------------------------------------------------------------------
    # Panel 4: Event counts (stacked bar)
    # ------------------------------------------------------------------
    ax_evts = fig.add_subplot(gs[1, 1])
    _style_ax(ax_evts, "PAT Event Counts", "events")
    bottoms = np.zeros(len(records))
    for key, label, color in [
        ("n_apnea",    EVT_APNEA,    EVT_COLORS[EVT_APNEA]),
        ("n_hypopnea", EVT_HYPOPNEA, EVT_COLORS[EVT_HYPOPNEA]),
        ("n_rera",     EVT_RERA,     EVT_COLORS[EVT_RERA]),
        ("n_central",  EVT_CENTRAL,  EVT_COLORS[EVT_CENTRAL]),
        ("n_pat",      EVT_PAT,      EVT_COLORS[EVT_PAT]),
    ]:
        vals = np.asarray([r[key] for r in records], dtype=float)
        if vals.sum() > 0:
            ax_evts.bar(xs, vals, bar_w, bottom=bottoms, color=color, label=label)
            bottoms += vals
    ax_evts.legend(fontsize=7, facecolor=BG_PANEL, labelcolor=FG, framealpha=0.7,
                   loc="upper left")
    _set_date_ticks(ax_evts, dates, records)
    ax_evts.set_xlim(-0.5, len(xs) - 0.5)

    # ------------------------------------------------------------------
    # Panel 5: Heart rate
    # ------------------------------------------------------------------
    ax_hr = fig.add_subplot(gs[2, 0])
    _style_ax(ax_hr, "Heart Rate", "bpm")
    _safe_line(ax_hr, xs, [r["hr_mean"] for r in records],
               color=HR_AVG_COLOR, marker="o", markersize=5, linewidth=1.5,
               label="Mean HR", zorder=3)
    _safe_line(ax_hr, xs, [r["hr_max"] for r in records],
               color=HR_MAX_COLOR, marker="^", markersize=4, linewidth=1.2,
               linestyle="--", label="Max HR", zorder=2)
    ax_hr.legend(fontsize=7, facecolor=BG_PANEL, labelcolor=FG, framealpha=0.7)
    _set_date_ticks(ax_hr, dates, records)
    ax_hr.set_xlim(-0.5, len(xs) - 0.5)

    # ------------------------------------------------------------------
    # Panel 6: SpO2
    # ------------------------------------------------------------------
    ax_spo2 = fig.add_subplot(gs[2, 1])
    _style_ax(ax_spo2, "SpO₂", "%")
    _safe_line(ax_spo2, xs, [r["spo2_mean"] for r in records],
               color=SPO2_AVG_COLOR, marker="o", markersize=5, linewidth=1.5,
               label="Mean SpO₂", zorder=3)
    _safe_line(ax_spo2, xs, [r["spo2_min"] for r in records],
               color="#e74c3c", marker="v", markersize=4, linewidth=1.2,
               linestyle="--", label="Min SpO₂", zorder=2)
    ax_spo2.axhline(90, color="#e74c3c", linewidth=0.7, linestyle=":", alpha=0.6)
    ax_spo2.axhline(95, color=FG,        linewidth=0.7, linestyle=":", alpha=0.4)
    ax_spo2.legend(fontsize=7, facecolor=BG_PANEL, labelcolor=FG, framealpha=0.7)
    _set_date_ticks(ax_spo2, dates, records)
    ax_spo2.set_xlim(-0.5, len(xs) - 0.5)
    all_spo2 = [r["spo2_min"] for r in records if np.isfinite(r["spo2_min"])]
    if all_spo2:
        ax_spo2.set_ylim(max(60, min(all_spo2) - 5), 101)

    # ------------------------------------------------------------------
    # Panel 7: Motion (avg) — bottom left
    # ------------------------------------------------------------------
    ax_motion = fig.add_subplot(gs[3, 0])
    _style_ax(ax_motion, "Average Motion", "level")
    motion_vals = [r["motion_mean"] for r in records]
    ax_motion.bar(xs, motion_vals, bar_w, color=MOTION_COLOR, alpha=0.85)
    _set_date_ticks(ax_motion, dates, records)
    ax_motion.set_xlim(-0.5, len(xs) - 0.5)

    # ------------------------------------------------------------------
    # Panel 8: Summary table — bottom right
    # ------------------------------------------------------------------
    ax_tbl = fig.add_subplot(gs[3, 1])
    ax_tbl.set_facecolor(BG_PANEL)
    ax_tbl.axis("off")
    for spine in ax_tbl.spines.values():
        spine.set_visible(False)

    if records:
        # Compute summary stats for the last 30 nights
        n = len(records)
        ahi_vals  = [r["ahi"]  for r in records if np.isfinite(r["ahi"])]
        pahi_vals = [r["pahi"] for r in records if np.isfinite(r["pahi"])]
        spo2_vals = [r["spo2_mean"] for r in records if np.isfinite(r["spo2_mean"])]
        hr_vals   = [r["hr_mean"]   for r in records if np.isfinite(r["hr_mean"])]
        dur_vals  = [r["duration_hours"] for r in records]

        def _fmt(vals, fmt=".1f"):
            if not vals:
                return "--"
            return f"{np.mean(vals):{fmt}}"

        lines = [
            f"Nights analysed:  {n}",
            f"Date range:       {dates[0].strftime('%b %-d')} – {dates[-1].strftime('%b %-d, %Y')}",
            "",
            f"Avg AHI:          {_fmt(ahi_vals)} /hr",
            f"Avg pAHI:         {_fmt(pahi_vals)} /hr",
            f"Avg duration:     {_fmt(dur_vals)} hr",
            f"Avg HR:           {_fmt(hr_vals)} bpm",
            f"Avg SpO₂:         {_fmt(spo2_vals)} %",
        ]
        ax_tbl.text(
            0.05, 0.95, "\n".join(lines),
            transform=ax_tbl.transAxes,
            fontsize=8.5, color=FG,
            verticalalignment="top",
            fontfamily="monospace",
        )
        ax_tbl.set_title("Summary", color=FG, fontsize=9, pad=4)

    return fig


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _pick_folder() -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title="Select folder with WatchPAT .dat files")
        root.destroy()
        return folder or None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Visualize WatchPAT ONE sleep metrics across multiple nights."
    )
    parser.add_argument("folder", nargs="?", help="Folder containing .dat files")
    args = parser.parse_args()

    folder = args.folder
    if not folder:
        folder = _pick_folder()
    if not folder:
        parser.print_usage()
        sys.exit(1)
    if not os.path.isdir(folder):
        print(f"Error: {folder!r} is not a directory.", file=sys.stderr)
        sys.exit(1)

    records = load_folder(folder)
    if not records:
        print("No usable recordings found.", file=sys.stderr)
        sys.exit(1)

    print(f"\nLoaded {len(records)} night(s). Rendering …")
    fig = build_figure(records)
    plt.show()


if __name__ == "__main__":
    main()
