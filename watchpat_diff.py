"""
Compare two WatchPAT `.dat` recordings and summarize their differences.

Usage:
    python watchpat_diff.py first.dat second.dat
    python watchpat_diff.py first.dat second.dat --json
"""

import argparse
import json
import math
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Optional

import watchpat_analysis
from watchpat_ble import parse_data_packet, read_dat_file


@dataclass
class RecordingSummary:
    path: str
    packet_count: int = 0
    duration_seconds: float = 0.0
    file_size_bytes: int = 0
    total_payload_bytes: int = 0
    waveform_samples: dict[str, int] = field(default_factory=dict)
    event_records: dict[str, int] = field(default_factory=dict)
    body_positions: dict[str, int] = field(default_factory=dict)
    metric_min: Optional[int] = None
    metric_max: Optional[int] = None
    metric_mean: Optional[float] = None
    hr_mean: Optional[float] = None
    hr_max: Optional[float] = None
    spo2_mean: Optional[float] = None
    spo2_min: Optional[float] = None
    ahi_estimate: Optional[float] = None
    pahi_estimate: Optional[float] = None
    rdi_estimate: Optional[float] = None
    apnea_events: int = 0
    central_events: int = 0
    pat_event_counts: dict[str, int] = field(default_factory=dict)
    sleep_stage_percentages: dict[str, float] = field(default_factory=dict)
    sleep_stage_counts: dict[str, int] = field(default_factory=dict)


def _normalize_data_payload(raw: bytes) -> bytes:
    """Accept either a full packet or an already-stripped DATA_PACKET payload."""
    if len(raw) >= 24 and raw[:2] == b"\xBB\xBB":
        return raw[24:]
    return raw


def _mean_or_none(values):
    return float(mean(values)) if values else None


def _max_or_none(values):
    return float(max(values)) if values else None


def _min_or_none(values):
    return float(min(values)) if values else None


def _float_or_none(value: float) -> Optional[float]:
    return None if value is None or value < 0 else float(value)


def _dominant_position(positions: dict[str, int]) -> str:
    if not positions:
        return "n/a"
    label, count = max(positions.items(), key=lambda item: item[1])
    total = sum(positions.values()) or 1
    return f"{label} ({count}/{total})"


def summarize_dat_file(path: str) -> RecordingSummary:
    buffers = watchpat_analysis.SensorBuffers()
    waveform_samples = Counter()
    event_records = Counter()
    body_positions = Counter()
    metric_values = []

    for idx, raw in enumerate(read_dat_file(path)):
        fake_now = float(idx + 1)
        payload = _normalize_data_payload(raw)
        pkt = parse_data_packet(payload, idx)
        for wf in pkt.waveforms:
            waveform_samples[wf.channel_name] += len(wf.samples)
        if pkt.motion is not None:
            label = watchpat_analysis.POSITION_LABELS.get(
                pkt.motion.body_position, pkt.motion.body_position)
            body_positions[label] += 1
        if pkt.metric is not None:
            metric_values.append(pkt.metric.value)
        for event in pkt.events:
            event_records[event.kind.name] += 1
        buffers.feed(pkt, now=fake_now)

    if buffers.packet_count > 0:
        with buffers.lock:
            buffers._last_derive_time = 0.0
            buffers._update_derived(
                now=float(buffers.packet_count + 1), record_history=False)

    hr_values = [v for v in buffers.hr_history if not math.isnan(v)]
    spo2_values = [v for v in buffers.spo2_full_history if not math.isnan(v)]
    pat_event_counts = Counter(ev[4] for ev in buffers.pat_events)
    sleep_stage_percentages = buffers.sleep_stage_percentages()
    sleep_stage_counts = Counter(buffers.sleep_stage_label_history)

    return RecordingSummary(
        path=path,
        packet_count=buffers.packet_count,
        duration_seconds=float(buffers.packet_count),
        file_size_bytes=os.path.getsize(path),
        total_payload_bytes=buffers.total_bytes,
        waveform_samples=dict(sorted(waveform_samples.items())),
        event_records=dict(sorted(event_records.items())),
        body_positions=dict(sorted(body_positions.items())),
        metric_min=min(metric_values) if metric_values else None,
        metric_max=max(metric_values) if metric_values else None,
        metric_mean=_mean_or_none(metric_values),
        hr_mean=_mean_or_none(hr_values),
        hr_max=_max_or_none(hr_values),
        spo2_mean=_mean_or_none(spo2_values),
        spo2_min=_min_or_none(spo2_values),
        ahi_estimate=_float_or_none(buffers.ahi_estimate),
        pahi_estimate=_float_or_none(buffers.pahi_estimate),
        rdi_estimate=_float_or_none(buffers.rdi_estimate),
        apnea_events=len(buffers.apnea_events),
        central_events=len(buffers.central_events),
        pat_event_counts=dict(sorted(pat_event_counts.items())),
        sleep_stage_percentages=sleep_stage_percentages,
        sleep_stage_counts={
            stage: sleep_stage_counts.get(stage, 0)
            for stage in watchpat_analysis.SLEEP_STAGE_ORDER
        },
    )


def _format_float(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _format_delta(a: Optional[float], b: Optional[float], digits: int = 2) -> str:
    if a is None or b is None:
        return "n/a"
    return f"{(b - a):+.{digits}f}"


def _metric_rows(left: RecordingSummary, right: RecordingSummary):
    rows = [
        ("Packets", str(left.packet_count), str(right.packet_count),
         f"{right.packet_count - left.packet_count:+d}"),
        ("Duration (min)", _format_float(left.duration_seconds / 60.0),
         _format_float(right.duration_seconds / 60.0),
         _format_delta(left.duration_seconds / 60.0, right.duration_seconds / 60.0)),
        ("Payload KB", _format_float(left.total_payload_bytes / 1024.0),
         _format_float(right.total_payload_bytes / 1024.0),
         _format_delta(left.total_payload_bytes / 1024.0,
                       right.total_payload_bytes / 1024.0)),
        ("AHI (/hr)", _format_float(left.ahi_estimate),
         _format_float(right.ahi_estimate),
         _format_delta(left.ahi_estimate, right.ahi_estimate)),
        ("pAHI (/hr)", _format_float(left.pahi_estimate),
         _format_float(right.pahi_estimate),
         _format_delta(left.pahi_estimate, right.pahi_estimate)),
        ("pRDI (/hr)", _format_float(left.rdi_estimate),
         _format_float(right.rdi_estimate),
         _format_delta(left.rdi_estimate, right.rdi_estimate)),
        ("Apnea events", str(left.apnea_events), str(right.apnea_events),
         f"{right.apnea_events - left.apnea_events:+d}"),
        ("Central events", str(left.central_events), str(right.central_events),
         f"{right.central_events - left.central_events:+d}"),
        ("PAT apnea", str(left.pat_event_counts.get(watchpat_analysis.EVT_APNEA, 0)),
         str(right.pat_event_counts.get(watchpat_analysis.EVT_APNEA, 0)),
         f"{right.pat_event_counts.get(watchpat_analysis.EVT_APNEA, 0) - left.pat_event_counts.get(watchpat_analysis.EVT_APNEA, 0):+d}"),
        ("PAT hypopnea", str(left.pat_event_counts.get(watchpat_analysis.EVT_HYPOPNEA, 0)),
         str(right.pat_event_counts.get(watchpat_analysis.EVT_HYPOPNEA, 0)),
         f"{right.pat_event_counts.get(watchpat_analysis.EVT_HYPOPNEA, 0) - left.pat_event_counts.get(watchpat_analysis.EVT_HYPOPNEA, 0):+d}"),
        ("RERA", str(left.pat_event_counts.get(watchpat_analysis.EVT_RERA, 0)),
         str(right.pat_event_counts.get(watchpat_analysis.EVT_RERA, 0)),
         f"{right.pat_event_counts.get(watchpat_analysis.EVT_RERA, 0) - left.pat_event_counts.get(watchpat_analysis.EVT_RERA, 0):+d}"),
        ("Mean HR", _format_float(left.hr_mean), _format_float(right.hr_mean),
         _format_delta(left.hr_mean, right.hr_mean)),
        ("Max HR", _format_float(left.hr_max), _format_float(right.hr_max),
         _format_delta(left.hr_max, right.hr_max)),
        ("Mean SpO2", _format_float(left.spo2_mean), _format_float(right.spo2_mean),
         _format_delta(left.spo2_mean, right.spo2_mean)),
        ("Min SpO2", _format_float(left.spo2_min), _format_float(right.spo2_min),
         _format_delta(left.spo2_min, right.spo2_min)),
        ("Metric mean", _format_float(left.metric_mean), _format_float(right.metric_mean),
         _format_delta(left.metric_mean, right.metric_mean)),
    ]
    for stage in watchpat_analysis.SLEEP_STAGE_ORDER:
        left_pct = left.sleep_stage_percentages.get(stage)
        right_pct = right.sleep_stage_percentages.get(stage)
        rows.append((
            f"{stage} (%)",
            _format_float(left_pct, 1),
            _format_float(right_pct, 1),
            _format_delta(left_pct, right_pct, 1),
        ))
    return rows


def format_comparison(left: RecordingSummary, right: RecordingSummary) -> str:
    lines = [
        f"Left : {left.path}",
        f"Right: {right.path}",
        "",
        f"{'Metric':<16} {'Left':>12} {'Right':>12} {'Delta':>12}",
        f"{'-' * 16} {'-' * 12} {'-' * 12} {'-' * 12}",
    ]
    for label, left_val, right_val, delta in _metric_rows(left, right):
        lines.append(f"{label:<16} {left_val:>12} {right_val:>12} {delta:>12}")

    lines.extend([
        "",
        "Waveform samples:",
        f"  Left : {left.waveform_samples}",
        f"  Right: {right.waveform_samples}",
        "",
        "Event records:",
        f"  Left : {left.event_records}",
        f"  Right: {right.event_records}",
        "",
        "Body position:",
        f"  Left : {_dominant_position(left.body_positions)}",
        f"  Right: {_dominant_position(right.body_positions)}",
        "",
        "Sleep stages (% of recording):",
        f"  Left : {left.sleep_stage_percentages}",
        f"  Right: {right.sleep_stage_percentages}",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", help="First `.dat` file")
    parser.add_argument("right", help="Second `.dat` file")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON instead of text")
    args = parser.parse_args()

    left = summarize_dat_file(args.left)
    right = summarize_dat_file(args.right)

    if args.json:
        print(json.dumps({"left": asdict(left), "right": asdict(right)}, indent=2))
        return

    print(format_comparison(left, right))


if __name__ == "__main__":
    main()
