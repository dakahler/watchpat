"""Android entry point: analyse a finished .dat recording and return a text summary."""

import math
from collections import Counter

from watchpat_analysis import SensorBuffers, POSITION_LABELS, EVT_APNEA, EVT_HYPOPNEA, EVT_RERA
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


def analyze(path: str) -> str:
    """Parse *path* and return a human-readable metrics summary string."""
    try:
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
                buffers._update_derived(now=float(buffers.packet_count + 1))

        hr_mean  = _mean(buffers.hr_history)
        hr_max   = max((v for v in buffers.hr_history if not math.isnan(v) and v > 0),
                       default=None)
        spo2_mean = _mean(buffers.spo2_full_history)
        spo2_min  = min((v for v in buffers.spo2_full_history if not math.isnan(v) and v > 0),
                        default=None)

        pat_counts = Counter(ev[4] for ev in buffers.pat_events)
        duration_min = buffers.packet_count / 60.0

        pos_total = sum(body_positions.values()) or 1
        pos_parts = ", ".join(
            f"{lbl} {100 * cnt // pos_total}%"
            for lbl, cnt in sorted(body_positions.items(),
                                   key=lambda kv: -kv[1])
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
            "======================",
        ]
        return "\n".join(lines)

    except Exception as exc:  # noqa: BLE001
        return f"Analysis failed: {exc}"
