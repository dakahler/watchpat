from __future__ import annotations

"""Android entry point: analyse a finished .dat recording and return a text summary."""

import json
import math
from collections import Counter

from watchpat_analysis import (
    SensorBuffers, POSITION_LABELS, EVT_APNEA, EVT_HYPOPNEA, EVT_RERA,
    SLEEP_STAGE_ORDER,
)
from watchpat_ble import parse_data_packet, read_dat_file

_HEADER_SIZE = 24


def _strip_header(raw: bytes) -> bytes:
    if len(raw) >= _HEADER_SIZE and raw[:2] == b"\xBB\xBB":
        return raw[_HEADER_SIZE:]
    return raw


def _fmt(value, digits=1, suffix=""):
    if value is None or (isinstance(value, float) and (math.isnan(value) or value < 0)):
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def _mean(lst):
    valid = [v for v in lst if not math.isnan(v) and v > 0]
    return sum(valid) / len(valid) if valid else None


def _clean_number(value, digits=2):
    if value is None or (isinstance(value, float) and (math.isnan(value) or value < 0)):
        return None
    return round(float(value), digits)


def _build_analysis(path: str) -> dict:
    buffers = SensorBuffers()
    body_positions: Counter = Counter()

    for idx, raw in enumerate(read_dat_file(path)):
        fake_now = float(idx + 1)
        pkt = parse_data_packet(_strip_header(raw), idx)
        if pkt.motion is not None:
            label = POSITION_LABELS.get(pkt.motion.body_position,
                                        pkt.motion.body_position)
            body_positions[label] += 1
        buffers.feed(pkt, now=fake_now)

    if buffers.packet_count > 0:
        with buffers.lock:
            buffers._last_derive_time = 0.0
            buffers._update_derived(
                now=float(buffers.packet_count + 1), record_history=False)

    hr_mean = _mean(buffers.hr_history)
    hr_max = max((v for v in buffers.hr_history if not math.isnan(v) and v > 0),
                 default=None)
    spo2_mean = _mean(buffers.spo2_full_history)
    spo2_min = min((v for v in buffers.spo2_full_history if not math.isnan(v) and v > 0),
                   default=None)

    pat_counts = Counter(ev[4] for ev in buffers.pat_events)
    duration_min = buffers.packet_count / 60.0
    stage_percentages = buffers.sleep_stage_percentages()
    stage_parts = ", ".join(
        f"{stage} {_fmt(stage_percentages.get(stage, 0.0), digits=1, suffix='%')}"
        for stage in SLEEP_STAGE_ORDER
    )

    pos_total = sum(body_positions.values()) or 1
    pos_parts = ", ".join(
        f"{lbl} {100 * cnt // pos_total}%"
        for lbl, cnt in sorted(body_positions.items(), key=lambda kv: -kv[1])
    )

    lines = [
        "=== Sleep Analysis ===",
        f"Duration:   {duration_min:.0f} min  ({buffers.packet_count:,} packets)",
        f"AHI:        {_fmt(buffers.ahi_estimate)} /hr",
        f"pAHI:       {_fmt(buffers.pahi_estimate)} /hr",
        f"pRDI:       {_fmt(buffers.rdi_estimate)} /hr",
        f"Apneas:     {len(buffers.apnea_events)}"
        f"  ({len(buffers.central_events)} central)",
        f"PAT events: {pat_counts.get(EVT_APNEA, 0)} apnea,"
        f" {pat_counts.get(EVT_HYPOPNEA, 0)} hypopnea,"
        f" {pat_counts.get(EVT_RERA, 0)} RERA",
        f"Mean HR:    {_fmt(hr_mean)} bpm",
        f"Max HR:     {_fmt(hr_max)} bpm",
        f"Mean SpO2:  {_fmt(spo2_mean)}%",
        f"Min SpO2:   {_fmt(spo2_min)}%",
        f"Position:   {pos_parts if pos_parts else 'n/a'}",
        f"Stages:     {stage_parts}",
        "======================",
    ]

    summary = {
        "recording_path": path,
        "packet_count": buffers.packet_count,
        "duration_minutes": round(duration_min, 2),
        "ahi": _clean_number(buffers.ahi_estimate, 2),
        "pahi": _clean_number(buffers.pahi_estimate, 2),
        "prdi": _clean_number(buffers.rdi_estimate, 2),
        "apnea_events": len(buffers.apnea_events),
        "central_events": len(buffers.central_events),
        "pat_apnea_events": pat_counts.get(EVT_APNEA, 0),
        "pat_hypopnea_events": pat_counts.get(EVT_HYPOPNEA, 0),
        "rera_events": pat_counts.get(EVT_RERA, 0),
        "mean_hr_bpm": _clean_number(hr_mean, 2),
        "max_hr_bpm": _clean_number(hr_max, 2),
        "mean_spo2": _clean_number(spo2_mean, 2),
        "min_spo2": _clean_number(spo2_min, 2),
        "body_positions": dict(body_positions),
        "sleep_stage_percentages": stage_percentages,
        "awake_pct": _clean_number(stage_percentages.get("Awake", 0.0), 2),
        "light_pct": _clean_number(stage_percentages.get("Light", 0.0), 2),
        "deep_pct": _clean_number(stage_percentages.get("Deep", 0.0), 2),
        "rem_pct": _clean_number(stage_percentages.get("REM", 0.0), 2),
    }
    return {
        "summary_text": "\n".join(lines),
        "summary": summary,
    }


def analyze(path: str) -> str:
    """Parse *path* and return a human-readable metrics summary string."""
    try:
        return _build_analysis(path)["summary_text"]
    except Exception as exc:  # noqa: BLE001
        return f"Analysis failed: {exc}"


def analyze_json(path: str) -> str:
    """Parse *path* and return structured JSON with summary + text."""
    try:
        return json.dumps(_build_analysis(path), sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "summary_text": f"Analysis failed: {exc}",
            "summary": {"recording_path": path, "error": str(exc)},
        }, sort_keys=True)
