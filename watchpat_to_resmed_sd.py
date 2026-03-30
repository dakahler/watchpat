"""
Convert a WatchPAT BLE `.dat` capture into a minimal ResMed SD-card image
that OSCAR can import via "Import from SD Card".

The generated card image is intentionally conservative:
- `Identification.tgt` is synthesized from the WatchPAT filename or CLI args.
- `STR.edf` contains only the mask on/off summary OSCAR needs to create a
  session timeline.
- `SAD.edf` contains a 1 Hz pulse series estimated heuristically from the
  WatchPAT pulse-like waveforms (preferring PAT when it is the cleanest).

This does not attempt to derive CPAP pressure, leak, or
event annotations from the WatchPAT raw stream.
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import struct
from array import array
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable


RECORD_SYNC = 0xAAAA
RECORD_HEADER_SIZE = 12

RECORD_KIND_OXIA = 0x0111
RECORD_KIND_OXIB = 0x0211
RECORD_KIND_PAT = 0x0311
RECORD_KIND_CHEST = 0x0401

WAVEFORM_KIND_NAMES = {
    RECORD_KIND_OXIA: "OxiA",
    RECORD_KIND_OXIB: "OxiB",
    RECORD_KIND_PAT: "PAT",
    RECORD_KIND_CHEST: "Chest",
}

DEFAULT_MODEL_NAME = "AirSense_10_AutoSet"
DEFAULT_MODEL_CODE = "37005"

FILENAME_RE = re.compile(
    r"watchpat_(?P<serial>\d+)_(?P<stamp>\d{8}_\d{6})\.dat$",
    re.IGNORECASE,
)
STAMP_RE = re.compile(r"(?P<stamp>\d{8}_\d{6})")


@dataclass
class SignalSamples:
    name: str
    samples: array
    rate_hz: int


@dataclass
class PulseCandidate:
    source_name: str
    peak_indices: list[int]
    interval_midpoints_s: list[float]
    interval_bpm: list[float]
    median_bpm: float
    mad_bpm: float
    score: float


@dataclass
class SpO2Estimate:
    red_name: str
    ir_name: str
    ratios: list[float]
    series: list[int]
    valid_count: int
    plausible_ratio_count: int
    score: float


@dataclass
class Segment:
    start_dt: datetime
    pulse_values: list[int]
    spo2_values: list[int]

    @property
    def end_dt(self) -> datetime:
        return self.start_dt + timedelta(seconds=len(self.pulse_values))


@dataclass
class EDFSignal:
    label: str
    physical_dimension: str
    physical_min: float
    physical_max: float
    digital_min: int
    digital_max: int
    samples_per_record: int
    values: list[int]
    transducer_type: str = ""
    prefiltering: str = ""
    reserved: str = ""


def infer_start_from_name(path: Path) -> datetime | None:
    match = FILENAME_RE.search(path.name)
    if match:
        return datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S")

    generic = STAMP_RE.search(path.name)
    if generic:
        return datetime.strptime(generic.group("stamp"), "%Y%m%d_%H%M%S")

    return None


def infer_serial_from_name(path: Path) -> str | None:
    match = FILENAME_RE.search(path.name)
    if match:
        return match.group("serial")
    return None


def read_dat_payloads(path: Path) -> Iterable[bytes]:
    with path.open("rb") as handle:
        while True:
            header = handle.read(4)
            if len(header) < 4:
                break
            length = struct.unpack("<I", header)[0]
            payload = handle.read(length)
            if len(payload) < length:
                break
            yield payload


def _zigzag_decode_8(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _signed_nibble(value: int) -> int:
    return value - 16 if value >= 8 else value


def decode_byte_delta_waveform(payload: bytes) -> list[int]:
    if len(payload) < 2:
        return []
    seed = struct.unpack_from("<h", payload, 0)[0]
    samples = [seed]
    acc = seed
    for value in payload[2:]:
        acc += _zigzag_decode_8(value)
        samples.append(acc)
    return samples


def decode_nibble_delta_waveform(payload: bytes) -> list[int]:
    if len(payload) < 3:
        return []
    seed = struct.unpack_from("<h", payload, 0)[0]
    samples = [seed]
    acc = seed
    for value in payload[3:]:
        low = value & 0x0F
        high = (value >> 4) & 0x0F
        acc += _signed_nibble(low)
        samples.append(acc)
        if high == 0 or high == 7:
            samples.append(acc)
    return samples


def parse_capture(path: Path) -> dict[str, SignalSamples]:
    channels = {
        "OxiA": SignalSamples("OxiA", array("h"), 100),
        "OxiB": SignalSamples("OxiB", array("h"), 100),
        "PAT": SignalSamples("PAT", array("h"), 100),
        "Chest": SignalSamples("Chest", array("h"), 100),
    }

    for packet_payload in read_dat_payloads(path):
        pos = 0
        while pos + RECORD_HEADER_SIZE <= len(packet_payload):
            sync = struct.unpack_from("<H", packet_payload, pos)[0]
            if sync != RECORD_SYNC:
                pos += 1
                continue

            record_id = packet_payload[pos + 2]
            record_type = packet_payload[pos + 3]
            payload_len = struct.unpack_from("<H", packet_payload, pos + 4)[0]
            rate = struct.unpack_from("<H", packet_payload, pos + 6)[0]
            payload_start = pos + RECORD_HEADER_SIZE
            payload_end = payload_start + payload_len
            if payload_end > len(packet_payload):
                break

            record_payload = packet_payload[payload_start:payload_end]
            kind = (record_id << 8) | record_type
            channel_name = WAVEFORM_KIND_NAMES.get(kind)
            if channel_name is not None:
                if kind == RECORD_KIND_CHEST:
                    decoded = decode_nibble_delta_waveform(record_payload)
                else:
                    decoded = decode_byte_delta_waveform(record_payload)
                if decoded:
                    channels[channel_name].rate_hz = rate or channels[channel_name].rate_hz
                    channels[channel_name].samples.extend(decoded)

            pos = payload_end

    return channels


def moving_average(samples: array, window: int) -> list[float]:
    if not samples:
        return []

    averages: list[float] = [0.0] * len(samples)
    total = 0.0
    queue: deque[int] = deque()
    for index, value in enumerate(samples):
        queue.append(value)
        total += value
        if len(queue) > window:
            total -= queue.popleft()
        averages[index] = total / len(queue)
    return averages


def detect_pulse_candidate(samples: array, sample_rate_hz: int, source_name: str) -> PulseCandidate | None:
    if len(samples) < sample_rate_hz * 10:
        return None

    baseline = moving_average(samples, max(3, int(sample_rate_hz * 0.75)))
    detrended = [sample - base for sample, base in zip(samples, baseline)]
    abs_median = median(abs(value) for value in detrended)
    threshold = max(20.0, abs_median * 1.5)
    refractory = max(1, int(sample_rate_hz * 0.35))
    prominence_window = max(3, int(sample_rate_hz * 0.15))

    peaks: list[int] = []
    last_peak = -refractory

    for index in range(1, len(detrended) - 1):
        if index - last_peak < refractory:
            continue
        center = detrended[index]
        if center <= threshold:
            continue
        if center <= detrended[index - 1] or center < detrended[index + 1]:
            continue

        lo = max(0, index - prominence_window)
        hi = min(len(detrended), index + prominence_window + 1)
        local_min = min(detrended[lo:hi])
        if (center - local_min) < (threshold * 1.2):
            continue

        peaks.append(index)
        last_peak = index

    if len(peaks) < 4:
        return None

    min_interval = int(sample_rate_hz * 60.0 / 140.0)
    max_interval = int(sample_rate_hz * 60.0 / 40.0)

    interval_midpoints_s: list[float] = []
    interval_bpm: list[float] = []
    for first, second in zip(peaks, peaks[1:]):
        delta = second - first
        if delta < min_interval or delta > max_interval:
            continue
        bpm = 60.0 * sample_rate_hz / delta
        interval_midpoints_s.append(((first + second) / 2.0) / sample_rate_hz)
        interval_bpm.append(bpm)

    if len(interval_bpm) < 4:
        return None

    median_bpm = median(interval_bpm)
    mad_bpm = median(abs(value - median_bpm) for value in interval_bpm)
    score = len(interval_bpm) / (mad_bpm + 1.0)

    return PulseCandidate(
        source_name=source_name,
        peak_indices=peaks,
        interval_midpoints_s=interval_midpoints_s,
        interval_bpm=interval_bpm,
        median_bpm=median_bpm,
        mad_bpm=mad_bpm,
        score=score,
    )


def choose_pulse_candidate(channels: dict[str, SignalSamples]) -> PulseCandidate:
    candidates: list[PulseCandidate] = []
    for name in ("PAT", "OxiB", "OxiA"):
        signal = channels[name]
        candidate = detect_pulse_candidate(signal.samples, signal.rate_hz, name)
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        raise ValueError("Unable to derive a pulse series from PAT/Oxi waveforms.")

    candidates.sort(key=lambda item: (item.score, len(item.interval_bpm)), reverse=True)
    return candidates[0]


def interpolate_pulse_series(candidate: PulseCandidate, duration_seconds: int) -> list[int]:
    if duration_seconds <= 0:
        return []

    midpoints = candidate.interval_midpoints_s
    bpm_values = candidate.interval_bpm
    if not midpoints:
        return [-1] * duration_seconds

    series: list[int] = []
    for second in range(duration_seconds):
        target = second + 0.5
        insert_at = bisect_left(midpoints, target)

        if insert_at == 0:
            time_gap = abs(midpoints[0] - target)
            value = bpm_values[0] if time_gap <= 5.0 else -1
        elif insert_at >= len(midpoints):
            time_gap = abs(target - midpoints[-1])
            value = bpm_values[-1] if time_gap <= 5.0 else -1
        else:
            left_t = midpoints[insert_at - 1]
            right_t = midpoints[insert_at]
            left_v = bpm_values[insert_at - 1]
            right_v = bpm_values[insert_at]
            if (target - left_t) > 5.0 and (right_t - target) > 5.0:
                value = -1
            elif right_t == left_t:
                value = left_v
            else:
                ratio = (target - left_t) / (right_t - left_t)
                value = left_v + ((right_v - left_v) * ratio)

        if value == -1:
            series.append(-1)
        else:
            series.append(int(round(max(30.0, min(180.0, value)))))

    return median_smooth(series, window_radius=2)


def median_smooth(values: list[int], window_radius: int) -> list[int]:
    smoothed: list[int] = []
    for index, value in enumerate(values):
        if value < 0:
            smoothed.append(value)
            continue
        window = [
            item
            for item in values[max(0, index - window_radius): index + window_radius + 1]
            if item >= 0
        ]
        smoothed.append(int(round(median(window))) if window else value)
    return smoothed


def fill_short_negative_gaps(values: list[int], max_gap: int, min_valid: int = 0) -> list[int]:
    filled = list(values)
    index = 0
    while index < len(filled):
        if filled[index] >= min_valid:
            index += 1
            continue

        gap_start = index
        while index < len(filled) and filled[index] < min_valid:
            index += 1
        gap_end = index
        gap_len = gap_end - gap_start

        left = filled[gap_start - 1] if gap_start > 0 and filled[gap_start - 1] >= min_valid else None
        right = filled[gap_end] if gap_end < len(filled) and filled[gap_end] >= min_valid else None
        if gap_len <= max_gap and left is not None and right is not None:
            for offset in range(gap_len):
                ratio = (offset + 1) / (gap_len + 1)
                filled[gap_start + offset] = int(round(left + ((right - left) * ratio)))

    return filled


def sinusoid_amplitude(samples: list[int] | list[float], hz: float, sample_rate_hz: int) -> float:
    if not samples:
        return 0.0

    mean_value = sum(samples) / len(samples)
    real = 0.0
    imag = 0.0
    for index, sample in enumerate(samples):
        centered = sample - mean_value
        angle = 2.0 * math.pi * hz * index / sample_rate_hz
        real += centered * math.cos(angle)
        imag -= centered * math.sin(angle)
    return (2.0 / len(samples)) * math.sqrt((real * real) + (imag * imag))


def score_spo2_assignment(ratios: list[float], series: list[int]) -> tuple[int, int, float]:
    valid_count = sum(1 for value in series if value >= 60)
    plausible_ratio_count = sum(1 for ratio in ratios if 0.4 <= ratio <= 1.3)
    outlier_count = sum(1 for ratio in ratios if ratio < 0.2 or ratio > 2.0)
    median_ratio = median(ratios) if ratios else 99.0
    score = (
        (plausible_ratio_count * 4.0)
        + (valid_count * 2.0)
        - (outlier_count * 2.0)
        - abs(median_ratio - 0.9) * 10.0
    )
    return valid_count, plausible_ratio_count, score


def derive_spo2_for_assignment(
    red_signal: SignalSamples,
    ir_signal: SignalSamples,
    pulse_series: list[int],
) -> SpO2Estimate:
    sample_rate_hz = red_signal.rate_hz
    max_len = min(len(red_signal.samples), len(ir_signal.samples))
    window = max(sample_rate_hz * 4, sample_rate_hz * 2)
    half_window = window // 2

    ratios: list[float] = []
    series: list[int] = []

    for second, bpm in enumerate(pulse_series):
        if bpm < 35:
            series.append(-1)
            continue

        center = int(second * sample_rate_hz + (sample_rate_hz / 2))
        start = max(0, center - half_window)
        end = min(max_len, center + half_window)
        if (end - start) < (sample_rate_hz * 2):
            series.append(-1)
            continue

        red_window = list(red_signal.samples[start:end])
        ir_window = list(ir_signal.samples[start:end])

        red_dc = sum(red_window) / len(red_window)
        ir_dc = sum(ir_window) / len(ir_window)
        if red_dc == 0 or ir_dc == 0:
            series.append(-1)
            continue

        pulse_hz = bpm / 60.0
        red_ac = sinusoid_amplitude(red_window, pulse_hz, sample_rate_hz)
        ir_ac = sinusoid_amplitude(ir_window, pulse_hz, sample_rate_hz)
        if red_ac <= 0.0 or ir_ac <= 0.0:
            series.append(-1)
            continue

        ratio = abs((red_ac / red_dc) / (ir_ac / ir_dc))
        ratios.append(ratio)
        spo2 = 116.0 - (25.0 * ratio)
        if 60.0 <= spo2 <= 100.0:
            series.append(int(round(spo2)))
        else:
            series.append(-1)

    series = fill_short_negative_gaps(series, max_gap=3, min_valid=60)
    series = median_smooth(series, window_radius=2)
    valid_count, plausible_ratio_count, score = score_spo2_assignment(ratios, series)
    return SpO2Estimate(
        red_name=red_signal.name,
        ir_name=ir_signal.name,
        ratios=ratios,
        series=series,
        valid_count=valid_count,
        plausible_ratio_count=plausible_ratio_count,
        score=score,
    )


def derive_spo2_series(channels: dict[str, SignalSamples], pulse_series: list[int]) -> SpO2Estimate:
    candidates = [
        derive_spo2_for_assignment(channels["OxiA"], channels["OxiB"], pulse_series),
        derive_spo2_for_assignment(channels["OxiB"], channels["OxiA"], pulse_series),
    ]
    candidates.sort(
        key=lambda item: (item.score, item.plausible_ratio_count, item.valid_count),
        reverse=True,
    )
    return candidates[0]


def resmed_day_start(dt: datetime) -> datetime:
    noon = dt.replace(hour=12, minute=0, second=0, microsecond=0)
    if dt < noon:
        noon -= timedelta(days=1)
    return noon


def split_by_resmed_day(start_dt: datetime, pulse_values: list[int], spo2_values: list[int]) -> list[Segment]:
    if len(pulse_values) != len(spo2_values):
        raise ValueError("Pulse and SpO2 series must have the same length.")

    segments: list[Segment] = []
    index = 0
    current_start = start_dt

    while index < len(pulse_values):
        day_end = resmed_day_start(current_start) + timedelta(days=1)
        seconds_until_boundary = int((day_end - current_start).total_seconds())
        chunk_len = min(len(pulse_values) - index, max(1, seconds_until_boundary))
        segments.append(
            Segment(
                start_dt=current_start,
                pulse_values=pulse_values[index:index + chunk_len],
                spo2_values=spo2_values[index:index + chunk_len],
            )
        )
        index += chunk_len
        current_start += timedelta(seconds=chunk_len)

    return segments


def ensure_empty_output_dir(output_dir: Path, force: bool) -> None:
    if output_dir.exists():
        if not force and any(output_dir.iterdir()):
            raise FileExistsError(
                f"Output directory already exists and is not empty: {output_dir}. "
                "Use --force to replace it."
            )
        if force:
            shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def write_identification_files(output_dir: Path, serial: str, model_name: str, model_code: str) -> None:
    ident_lines = [
        f"#SRN {serial}",
        f"#PNA {model_name}",
        f"#PCD {model_code}",
    ]
    (output_dir / "Identification.tgt").write_text("\n".join(ident_lines) + "\n", encoding="ascii")
    (output_dir / "Identification.crc").write_bytes(b"")


def edf_ascii(value: object, width: int) -> bytes:
    text = str(value)
    if len(text) > width:
        text = text[:width]
    return text.ljust(width).encode("ascii")


def format_edf_number(value: float | int, width: int = 8) -> str:
    if isinstance(value, float) and not value.is_integer():
        text = f"{value:.6f}".rstrip("0").rstrip(".")
    else:
        text = str(int(value))
    if len(text) > width:
        raise ValueError(f"EDF field value {text!r} exceeds {width} characters.")
    return text


def write_edf(
    path: Path,
    start_dt: datetime,
    record_duration_seconds: int,
    num_records: int,
    recording_ident: str,
    signals: list[EDFSignal],
) -> None:
    if num_records <= 0:
        raise ValueError("EDF requires at least one data record.")
    if not signals:
        raise ValueError("EDF requires at least one signal.")

    for signal in signals:
        expected = signal.samples_per_record * num_records
        if len(signal.values) != expected:
            raise ValueError(
                f"{signal.label} has {len(signal.values)} samples, expected {expected} "
                f"({signal.samples_per_record} x {num_records})."
            )
        for value in signal.values:
            if value < -32768 or value > 32767:
                raise ValueError(f"{signal.label} sample {value} is outside int16 range.")

    num_signals = len(signals)
    header_bytes = 256 + (num_signals * 256)
    patient_ident = "X X X WatchPAT X"

    header = bytearray()
    header.extend(edf_ascii("0", 8))
    header.extend(edf_ascii(patient_ident, 80))
    header.extend(edf_ascii(recording_ident, 80))
    header.extend(edf_ascii(start_dt.strftime("%d.%m.%y"), 8))
    header.extend(edf_ascii(start_dt.strftime("%H.%M.%S"), 8))
    header.extend(edf_ascii(header_bytes, 8))
    header.extend(edf_ascii("", 44))
    header.extend(edf_ascii(num_records, 8))
    header.extend(edf_ascii(format_edf_number(record_duration_seconds), 8))
    header.extend(edf_ascii(num_signals, 4))

    for signal in signals:
        header.extend(edf_ascii(signal.label, 16))
    for signal in signals:
        header.extend(edf_ascii(signal.transducer_type, 80))
    for signal in signals:
        header.extend(edf_ascii(signal.physical_dimension, 8))
    for signal in signals:
        header.extend(edf_ascii(format_edf_number(signal.physical_min), 8))
    for signal in signals:
        header.extend(edf_ascii(format_edf_number(signal.physical_max), 8))
    for signal in signals:
        header.extend(edf_ascii(format_edf_number(signal.digital_min), 8))
    for signal in signals:
        header.extend(edf_ascii(format_edf_number(signal.digital_max), 8))
    for signal in signals:
        header.extend(edf_ascii(signal.prefiltering, 80))
    for signal in signals:
        header.extend(edf_ascii(signal.samples_per_record, 8))
    for signal in signals:
        header.extend(edf_ascii(signal.reserved, 32))

    with path.open("wb") as handle:
        handle.write(header)
        for record_index in range(num_records):
            for signal in signals:
                offset = record_index * signal.samples_per_record
                chunk = signal.values[offset:offset + signal.samples_per_record]
                handle.write(struct.pack(f"<{len(chunk)}h", *chunk))


def build_str_records(segments: list[Segment]) -> tuple[datetime, int, list[int], list[int], list[int]]:
    if not segments:
        raise ValueError("At least one segment is required to build STR.edf.")

    day_starts = [resmed_day_start(segment.start_dt) for segment in segments]
    first_day = day_starts[0]
    last_day = day_starts[-1]
    num_records = int((last_day - first_day).total_seconds() // 86400) + 1

    mask_on = [0] * num_records
    mask_off = [0] * num_records
    mask_events = [0] * num_records

    for segment, day_start in zip(segments, day_starts):
        record_index = int((day_start - first_day).total_seconds() // 86400)
        on_minutes = int(math.floor((segment.start_dt - day_start).total_seconds() / 60.0))
        off_minutes = int(math.ceil((segment.end_dt - day_start).total_seconds() / 60.0))
        off_minutes = min(off_minutes, 24 * 60)
        if off_minutes == on_minutes:
            off_minutes = min(24 * 60, on_minutes + 1)
        mask_on[record_index] = on_minutes
        mask_off[record_index] = off_minutes
        mask_events[record_index] = 2

    return first_day, num_records, mask_on, mask_off, mask_events


def write_str_edf(output_dir: Path, serial: str, segments: list[Segment]) -> None:
    start_dt, num_records, mask_on, mask_off, mask_events = build_str_records(segments)
    signals = [
        EDFSignal(
            label="Mask On",
            physical_dimension="min",
            physical_min=0,
            physical_max=1440,
            digital_min=0,
            digital_max=1440,
            samples_per_record=1,
            values=mask_on,
        ),
        EDFSignal(
            label="Mask Off",
            physical_dimension="min",
            physical_min=0,
            physical_max=1440,
            digital_min=0,
            digital_max=1440,
            samples_per_record=1,
            values=mask_off,
        ),
        EDFSignal(
            label="Mask Events",
            physical_dimension="count",
            physical_min=0,
            physical_max=20,
            digital_min=0,
            digital_max=20,
            samples_per_record=1,
            values=mask_events,
        ),
    ]
    write_edf(
        output_dir / "STR.edf",
        start_dt=start_dt,
        record_duration_seconds=24 * 60 * 60,
        num_records=num_records,
        recording_ident=f"WatchPAT conversion SRN={serial}",
        signals=signals,
    )
    (output_dir / "STR.crc").write_bytes(b"")


def write_sad_edf(path: Path, serial: str, segment: Segment) -> None:
    signals = [
        EDFSignal(
            label="SpO2.1s",
            physical_dimension="%",
            physical_min=-1,
            physical_max=100,
            digital_min=-1,
            digital_max=100,
            samples_per_record=1,
            values=segment.spo2_values,
            prefiltering="heuristic WatchPAT SpO2",
        ),
        EDFSignal(
            label="Pulse.1s",
            physical_dimension="bpm",
            physical_min=-1,
            physical_max=255,
            digital_min=-1,
            digital_max=255,
            samples_per_record=1,
            values=segment.pulse_values,
            prefiltering="heuristic WatchPAT pulse",
        )
    ]
    write_edf(
        path,
        start_dt=segment.start_dt,
        record_duration_seconds=1,
        num_records=len(segment.pulse_values),
        recording_ident=f"WatchPAT conversion SRN={serial}",
        signals=signals,
    )


def build_output(
    output_dir: Path,
    serial: str,
    model_name: str,
    model_code: str,
    start_dt: datetime,
    pulse_series: list[int],
    spo2_series: list[int],
) -> list[Segment]:
    segments = split_by_resmed_day(start_dt, pulse_series, spo2_series)
    datalog_dir = output_dir / "DATALOG"
    datalog_dir.mkdir(parents=True, exist_ok=True)

    write_identification_files(output_dir, serial, model_name, model_code)
    write_str_edf(output_dir, serial, segments)

    for segment in segments:
        filename = f"{segment.start_dt:%Y%m%d_%H%M%S}_SAD.edf"
        write_sad_edf(datalog_dir / filename, serial, segment)

    return segments


def summarize_duration(channels: dict[str, SignalSamples]) -> float:
    durations = [
        len(signal.samples) / signal.rate_hz
        for signal in channels.values()
        if signal.samples and signal.rate_hz > 0
    ]
    if not durations:
        raise ValueError("No waveform samples were decoded from the .dat file.")
    return max(durations)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert WatchPAT BLE .dat captures into a ResMed SD image for OSCAR.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_dat", type=Path, help="WatchPAT .dat capture to convert")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Destination directory for the generated ResMed SD-card image",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="",
        help="Override capture start time (YYYY-MM-DD HH:MM:SS or YYYYMMDD_HHMMSS)",
    )
    parser.add_argument(
        "--serial",
        type=str,
        default="",
        help="Override the serial used in Identification.tgt and EDF recording IDs",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=DEFAULT_MODEL_NAME,
        help="ResMed product name to place in Identification.tgt",
    )
    parser.add_argument(
        "--model-code",
        type=str,
        default=DEFAULT_MODEL_CODE,
        help="ResMed product code to place in Identification.tgt",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the output directory if it already exists",
    )
    return parser.parse_args()


def parse_start_override(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("Empty start override.")

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d_%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(
        "Unsupported --start format. Use 'YYYY-MM-DD HH:MM:SS' or 'YYYYMMDD_HHMMSS'."
    )


def main() -> None:
    args = parse_args()
    input_dat: Path = args.input_dat
    if not input_dat.is_file():
        raise FileNotFoundError(f"Input file not found: {input_dat}")

    start_dt = parse_start_override(args.start) if args.start else infer_start_from_name(input_dat)
    if start_dt is None:
        raise ValueError(
            "Unable to infer the capture start time from the filename. Use --start."
        )

    serial = args.serial.strip() or infer_serial_from_name(input_dat)
    if not serial:
        raise ValueError(
            "Unable to infer the device serial from the filename. Use --serial."
        )

    output_dir = args.output_dir or input_dat.with_name(f"{input_dat.stem}_resmed_sd")
    ensure_empty_output_dir(output_dir, force=args.force)

    channels = parse_capture(input_dat)
    capture_duration_s = summarize_duration(channels)
    candidate = choose_pulse_candidate(channels)
    pulse_length_s = max(1, int(round(capture_duration_s)))
    pulse_series = interpolate_pulse_series(candidate, pulse_length_s)
    spo2_estimate = derive_spo2_series(channels, pulse_series)

    # Reject pathological pulse output before writing files.
    valid_pulse = [value for value in pulse_series if value >= 0]
    if not valid_pulse:
        raise ValueError("Pulse derivation failed; no usable pulse values were produced.")
    valid_spo2 = [value for value in spo2_estimate.series if value >= 60]
    if not valid_spo2:
        raise ValueError("SpO2 derivation failed; no usable SpO2 values were produced.")

    segments = build_output(
        output_dir=output_dir,
        serial=serial,
        model_name=args.model_name,
        model_code=args.model_code,
        start_dt=start_dt,
        pulse_series=pulse_series,
        spo2_series=spo2_estimate.series,
    )

    print(f"Input:        {input_dat}")
    print(f"Output:       {output_dir}")
    print(f"Start:        {start_dt:%Y-%m-%d %H:%M:%S}")
    print(f"Serial:       {serial}")
    print(f"Pulse source: {candidate.source_name}")
    print(f"Pulse median: {candidate.median_bpm:.1f} bpm")
    print(f"Pulse MAD:    {candidate.mad_bpm:.1f} bpm")
    print(f"SpO2 map:     red={spo2_estimate.red_name} ir={spo2_estimate.ir_name}")
    print(f"SpO2 valid:   {spo2_estimate.valid_count} seconds")
    print(f"SpO2 median:  {median(valid_spo2):.1f} %")
    print(f"Duration:     {len(pulse_series)} seconds")
    print(f"Segments:     {len(segments)}")
    for segment in segments:
        print(
            f"  {segment.start_dt:%Y-%m-%d %H:%M:%S} -> "
            f"{segment.end_dt:%Y-%m-%d %H:%M:%S} "
            f"({len(segment.pulse_values)} s)"
        )


if __name__ == "__main__":
    main()
