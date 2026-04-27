from __future__ import annotations

"""
Pure-analysis primitives for WatchPAT ONE recordings.

Contains only stdlib + NumPy — no matplotlib or GUI dependencies —
so this module can be imported on Android via Chaquopy as well as on
the desktop where watchpat_gui.py adds the live rendering layer.
"""

import math
import threading
import time
from collections import Counter, deque

import numpy as np

# ---------------------------------------------------------------------------
# Sensor configuration
# ---------------------------------------------------------------------------
WINDOW_SECONDS  = 10
WAVEFORM_RATE   = 100
MOTION_RATE     = 5
BUFFER_SIZE     = WINDOW_SECONDS * WAVEFORM_RATE   # 1000 samples
MOTION_BUFFER   = WINDOW_SECONDS * MOTION_RATE     # 50 samples
DERIVED_HISTORY = 120
DERIVE_INTERVAL = 1.0

POSITION_LABELS = {
    "z+": "Supine",
    "z-": "Prone",
    "y+": "Left",
    "y-": "Right",
    "x+": "Upright",
    "x-": "Inverted",
}

EVT_APNEA    = "APNEA"
EVT_HYPOPNEA = "HYPOPNEA"
EVT_RERA     = "RERA"
EVT_PAT      = "PAT"
EVT_CENTRAL  = "CENTRAL"

SLEEP_STAGE_AWAKE = "Awake"
SLEEP_STAGE_LIGHT = "Light"
SLEEP_STAGE_DEEP = "Deep"
SLEEP_STAGE_REM = "REM"
SLEEP_STAGE_ORDER = [
    SLEEP_STAGE_AWAKE,
    SLEEP_STAGE_LIGHT,
    SLEEP_STAGE_DEEP,
    SLEEP_STAGE_REM,
]
SLEEP_STAGE_LEVELS = {
    SLEEP_STAGE_DEEP: 0,
    SLEEP_STAGE_LIGHT: 1,
    SLEEP_STAGE_REM: 2,
    SLEEP_STAGE_AWAKE: 3,
}


# ---------------------------------------------------------------------------
# Signal-processing helpers (used internally by SensorBuffers)
# ---------------------------------------------------------------------------

def _box_mean(a: np.ndarray, win: int) -> np.ndarray:
    """O(n) sliding box-mean, identical output to np.convolve(a, ones(win)/win, 'same')."""
    pad_l = (win - 1) // 2
    pad_r = win - 1 - pad_l
    padded = np.pad(np.asarray(a, dtype=float), (pad_l, pad_r))
    cs = np.zeros(len(padded) + 1, dtype=float)
    np.cumsum(padded, out=cs[1:])
    return (cs[win:] - cs[:-win]) / win


def _detect_peaks_rt(samples: np.ndarray, rate: int) -> list:
    if len(samples) < rate * 3:
        return []
    win = max(3, int(rate * 0.75))
    baseline = _box_mean(samples, win)
    detrended = samples - baseline

    abs_median = float(np.median(np.abs(detrended)))
    threshold = max(20.0, abs_median * 1.5)
    refractory = max(1, int(rate * 0.35))
    prom_win = max(3, int(rate * 0.15))

    # Vectorised pre-filter: only local maxima above the threshold become candidates.
    # The refractory-period and prominence checks then run on this sparse set.
    mask = (
        (detrended[1:-1] > threshold)
        & (detrended[1:-1] > detrended[:-2])
        & (detrended[1:-1] >= detrended[2:])
    )
    candidates = np.where(mask)[0] + 1  # shift back to detrended indices

    peaks: list = []
    last_peak = -refractory
    prom_thresh = threshold * 1.2
    n = len(detrended)
    for i in candidates:
        if i - last_peak < refractory:
            continue
        lo = max(0, i - prom_win)
        hi = min(n, i + prom_win + 1)
        if detrended[i] - float(np.min(detrended[lo:hi])) >= prom_thresh:
            peaks.append(int(i))
            last_peak = i
    return peaks


def _compute_heart_rate(samples: np.ndarray, rate: int) -> float:
    peaks = _detect_peaks_rt(samples, rate)
    if len(peaks) < 3:
        return -1.0
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


def _compute_spo2_pair(red: np.ndarray, ir: np.ndarray, bpm: float,
                       rate: int) -> tuple:
    n = min(len(red), len(ir), rate * 4)
    if n < rate * 2:
        return -1.0, -1.0
    red_win = red[-n:]
    ir_win = ir[-n:]

    red_dc = float(np.mean(red_win))
    ir_dc = float(np.mean(ir_win))
    if abs(red_dc) < 10.0 or abs(ir_dc) < 10.0:
        return -1.0, -1.0

    angle = (2.0 * math.pi * (bpm / 60.0) / rate) * np.arange(n)
    cos_t = np.cos(angle)
    sin_t = np.sin(angle)

    def _ac(win, dc):
        c = win - dc
        return (2.0 / n) * math.sqrt(float(np.dot(c, cos_t))**2 + float(np.dot(c, sin_t))**2)

    red_ac = _ac(red_win, red_dc)
    ir_ac = _ac(ir_win, ir_dc)
    if red_ac <= 0 or ir_ac <= 0:
        return -1.0, -1.0

    if red_ac / abs(red_dc) > 0.5 or ir_ac / abs(ir_dc) > 0.5:
        return -1.0, -1.0

    ratio = abs((red_ac / red_dc) / (ir_ac / ir_dc))
    spo2 = 116.0 - 25.0 * ratio
    if 60.0 <= spo2 <= 100.0:
        return spo2, ratio
    return -1.0, ratio


def _compute_spo2_dual(a: np.ndarray, b: np.ndarray, bpm: float,
                       rate: int) -> tuple[float, float, float, float]:
    """Compute SpO2 for both (a,b) and (b,a) orderings, sharing one sinusoid pass."""
    n = min(len(a), len(b), rate * 4)
    if n < rate * 2:
        return -1.0, -1.0, -1.0, -1.0
    a_win = a[-n:]
    b_win = b[-n:]
    a_dc = float(np.mean(a_win))
    b_dc = float(np.mean(b_win))
    if abs(a_dc) < 10.0 or abs(b_dc) < 10.0:
        return -1.0, -1.0, -1.0, -1.0

    angle = (2.0 * math.pi * (bpm / 60.0) / rate) * np.arange(n)
    cos_t = np.cos(angle)
    sin_t = np.sin(angle)

    def _ac(win, dc):
        c = win - dc
        return (2.0 / n) * math.sqrt(float(np.dot(c, cos_t))**2 + float(np.dot(c, sin_t))**2)

    a_ac = _ac(a_win, a_dc)
    b_ac = _ac(b_win, b_dc)

    def _spo2_ratio(red_ac, red_dc, ir_ac, ir_dc):
        if red_ac <= 0 or ir_ac <= 0:
            return -1.0, -1.0
        if red_ac / abs(red_dc) > 0.5 or ir_ac / abs(ir_dc) > 0.5:
            return -1.0, -1.0
        ratio = abs((red_ac / red_dc) / (ir_ac / ir_dc))
        spo2 = 116.0 - 25.0 * ratio
        return (spo2, ratio) if 60.0 <= spo2 <= 100.0 else (-1.0, ratio)

    spo2_ab, ratio_ab = _spo2_ratio(a_ac, a_dc, b_ac, b_dc)
    spo2_ba, ratio_ba = _spo2_ratio(b_ac, b_dc, a_ac, a_dc)
    return spo2_ab, ratio_ab, spo2_ba, ratio_ba


def _compute_motion_level(ax: deque, ay: deque, az: deque) -> float:
    n = min(len(ax), len(ay), len(az), MOTION_RATE * 10)
    if n < 4:
        return -1.0
    x = np.asarray(list(ax)[-n:], dtype=float)
    y = np.asarray(list(ay)[-n:], dtype=float)
    z = np.asarray(list(az)[-n:], dtype=float)
    deltas = np.sqrt(np.diff(x) ** 2 + np.diff(y) ** 2 + np.diff(z) ** 2)
    if len(deltas) == 0:
        return -1.0
    return float(np.mean(deltas))


def _compute_resp_features(samples: np.ndarray, rate: int) -> tuple[float, float, float]:
    n = min(len(samples), rate * 10)
    if n < rate * 4:
        return -1.0, -1.0, -1.0
    window = samples[-n:].astype(float)
    smooth_win = max(3, rate // 2)
    baseline = _box_mean(window, smooth_win)
    detrended = window - baseline
    amp = float(np.percentile(detrended, 90) - np.percentile(detrended, 10))
    if amp <= 0:
        return -1.0, -1.0, -1.0
    signs = detrended >= 0
    crossings = int(np.count_nonzero(signs[1:] != signs[:-1]))
    duration_s = n / float(rate)
    bpm = (crossings / 2.0) * 60.0 / duration_s if duration_s > 0 else -1.0
    if bpm < 4.0 or bpm > 40.0:
        bpm = -1.0
    env_win = max(3, rate)
    envelope = _box_mean(np.abs(detrended), env_win)
    env_mean = float(np.mean(envelope))
    variability = float(np.std(envelope) / env_mean) if env_mean > 1e-6 else -1.0
    return bpm, amp, variability


# ---------------------------------------------------------------------------
# Thread-safe data buffer
# ---------------------------------------------------------------------------

class SensorBuffers:
    """Thread-safe rolling buffers for all sensor channels."""

    def __init__(self):
        self.lock = threading.Lock()

        self.oxi_a = deque(maxlen=BUFFER_SIZE)
        self.oxi_b = deque(maxlen=BUFFER_SIZE)
        self.pat = deque(maxlen=BUFFER_SIZE)
        self.chest = deque(maxlen=BUFFER_SIZE)

        self.accel_x = deque(maxlen=MOTION_BUFFER)
        self.accel_y = deque(maxlen=MOTION_BUFFER)
        self.accel_z = deque(maxlen=MOTION_BUFFER)
        self.field_a = deque(maxlen=MOTION_BUFFER)
        self.field_b = deque(maxlen=MOTION_BUFFER)

        self.hr_history = deque(maxlen=DERIVED_HISTORY)
        self.spo2_history = deque(maxlen=DERIVED_HISTORY)
        self.current_hr = -1.0
        self.current_spo2 = -1.0
        self._last_derive_time = 0.0
        self._spo2_score_ab = 0.0
        self._spo2_score_ba = 0.0
        self._spo2_raw = deque(maxlen=15)
        self._spo2_ema = -1.0
        self.hr_full_history: deque = deque(maxlen=4 * 3600)
        self.hr_full_times: deque = deque(maxlen=4 * 3600)
        self.motion_level_history: deque = deque(maxlen=4 * 3600)
        self.motion_level_times: deque = deque(maxlen=4 * 3600)
        self.resp_rate_history: deque = deque(maxlen=4 * 3600)
        self.resp_amp_history: deque = deque(maxlen=4 * 3600)
        self.resp_variability_history: deque = deque(maxlen=4 * 3600)
        self.sleep_stage_history: deque = deque(maxlen=4 * 3600)
        self.sleep_stage_times: deque = deque(maxlen=4 * 3600)
        self.sleep_stage_label_history: deque = deque(maxlen=4 * 3600)
        self.current_sleep_stage = SLEEP_STAGE_AWAKE

        self.body_position = "?"
        self.body_position_label = "Unknown"
        self.metric_value = 0
        self.packet_count = 0
        self.total_bytes = 0
        self.start_time = None
        self.last_motion_crc_ok = 0
        self.last_motion_crc_total = 0
        self.events = deque(maxlen=20)

        self.spo2_full_history: deque = deque(maxlen=4 * 3600)
        self.spo2_full_times: deque = deque(maxlen=4 * 3600)
        self.apnea_events: list = []
        self.apnea_event_count = 0
        self.ahi_estimate = -1.0
        self._desat_baseline: deque = deque(maxlen=120)
        self._desat_in_event = False
        self._desat_start_elapsed = 0.0
        self._desat_nadir = 100.0

        self.pat_amp_history: deque = deque(maxlen=4 * 3600)
        self.pat_amp_times: deque = deque(maxlen=4 * 3600)
        self.pat_events: list = []
        self.pat_event_type_counts: Counter = Counter()
        self.pahi_estimate = -1.0
        self._pat_baseline_buf: deque = deque(maxlen=300)
        self._pat_in_event = False
        self._pat_event_start = 0.0
        self._pat_hr_baseline = -1.0
        self.central_events: list = []
        self.central_event_count = 0
        self.rdi_estimate = -1.0
        self.sleep_stage_counts: Counter = Counter()
        self.sleep_stage_total = 0
        self._sleep_stage_context_at = -1.0
        self._sleep_stage_context = None
        self._resp_cache: tuple = (-1.0, -1.0, -1.0)
        self._resp_cache_time: float = 0.0

    def feed(self, pkt, now: float = None):
        """Ingest a parsed data packet into the rolling buffers."""
        with self.lock:
            if now is None:
                now = time.time()
            if self.start_time is None:
                self.start_time = now
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

            if now - self._last_derive_time >= DERIVE_INTERVAL:
                self._last_derive_time = now
                self._update_derived(now=now)

    def _recent_valid_array(self, values: deque, limit: int) -> np.ndarray:
        if not values:
            return np.array([])
        sliced = list(values)[-limit:]
        arr = np.asarray(sliced, dtype=float)
        return arr[~np.isnan(arr)]

    def _sleep_stage_thresholds(self, elapsed: float) -> dict[str, float]:
        if (
            self._sleep_stage_context is not None
            and elapsed - self._sleep_stage_context_at < 15.0
        ):
            return self._sleep_stage_context

        long_hr = self._recent_valid_array(self.hr_full_history, 600)
        long_motion = self._recent_valid_array(self.motion_level_history, 600)
        long_resp_var = self._recent_valid_array(self.resp_variability_history, 600)
        long_pat = self._recent_valid_array(self.pat_amp_history, 600)
        ctx = {
            "hr_low": float(np.percentile(long_hr, 35)) if long_hr.size else -1.0,
            "hr_high": float(np.percentile(long_hr, 70)) if long_hr.size else -1.0,
            "motion_low": (
                float(np.percentile(long_motion, 35)) if long_motion.size else -1.0
            ),
            "motion_high": (
                float(np.percentile(long_motion, 75)) if long_motion.size else -1.0
            ),
            "resp_low": (
                float(np.percentile(long_resp_var, 35)) if long_resp_var.size else -1.0
            ),
            "resp_high": (
                float(np.percentile(long_resp_var, 70)) if long_resp_var.size else -1.0
            ),
            "pat_low": float(np.percentile(long_pat, 30)) if long_pat.size else -1.0,
            "pat_high": float(np.percentile(long_pat, 70)) if long_pat.size else -1.0,
        }
        self._sleep_stage_context = ctx
        self._sleep_stage_context_at = elapsed
        return ctx

    def _estimate_sleep_stage(
        self,
        motion_level: float,
        resp_variability: float,
        pat_ratio: float,
        elapsed: float,
    ) -> str:
        if len(self.hr_full_history) < 30:
            return SLEEP_STAGE_AWAKE

        recent_hr = self._recent_valid_array(self.hr_full_history, 30)
        recent_motion = self._recent_valid_array(self.motion_level_history, 30)
        recent_resp_var = self._recent_valid_array(self.resp_variability_history, 30)
        recent_spo2 = self._recent_valid_array(self.spo2_full_history, 30)
        recent_pat = self._recent_valid_array(self.pat_amp_history, 30)

        if recent_hr.size < 10 or recent_motion.size < 10:
            return SLEEP_STAGE_AWAKE

        ctx = self._sleep_stage_thresholds(elapsed)
        hr_now = float(np.mean(recent_hr[-10:]))
        hr_std = float(np.std(recent_hr[-30:])) if recent_hr.size >= 5 else 0.0
        hr_low = ctx["hr_low"] if ctx["hr_low"] >= 0 else hr_now
        hr_high = ctx["hr_high"] if ctx["hr_high"] >= 0 else hr_now
        motion_now = float(np.mean(recent_motion[-10:]))
        motion_low = ctx["motion_low"] if ctx["motion_low"] >= 0 else motion_now
        motion_high = ctx["motion_high"] if ctx["motion_high"] >= 0 else motion_now
        resp_now = (
            float(np.mean(recent_resp_var[-10:])) if recent_resp_var.size else resp_variability
        )
        pat_now = float(np.mean(recent_pat[-10:])) if recent_pat.size else pat_ratio
        resp_low = ctx["resp_low"] if ctx["resp_low"] >= 0 else resp_now
        resp_high = ctx["resp_high"] if ctx["resp_high"] >= 0 else resp_now
        pat_low = ctx["pat_low"] if ctx["pat_low"] >= 0 else pat_now
        pat_high = ctx["pat_high"] if ctx["pat_high"] >= 0 else pat_now
        spo2_drop = 0.0
        if recent_spo2.size:
            spo2_drop = float(max(0.0, np.max(recent_spo2) - np.min(recent_spo2)))

        if motion_now >= max(2.6, motion_high * 1.35):
            return SLEEP_STAGE_AWAKE
        if hr_std >= 6.0 and motion_now > max(1.8, motion_low * 1.15):
            return SLEEP_STAGE_AWAKE
        deep_support = (
            hr_std <= 2.8
            or resp_now <= resp_low
            or pat_now >= pat_high
        )
        if (
            motion_now <= max(1.35, motion_low * 1.02)
            and hr_now <= hr_low + 1.5
            and deep_support
            and spo2_drop < 1.5
        ):
            return SLEEP_STAGE_DEEP
        rem_score = 0
        if hr_now >= hr_high:
            rem_score += 1
        if hr_std >= 3.5:
            rem_score += 1
        if resp_now >= resp_high:
            rem_score += 1
        if pat_now > 0 and pat_now <= pat_low:
            rem_score += 1
        if motion_now <= max(1.8, motion_low * 1.2) and rem_score >= 2:
            return SLEEP_STAGE_REM
        return SLEEP_STAGE_LIGHT

    def sleep_stage_percentages(self) -> dict[str, float]:
        total = self.sleep_stage_total
        counts = self.sleep_stage_counts
        if total <= 0 and self.sleep_stage_label_history:
            total = len(self.sleep_stage_label_history)
            counts = Counter(self.sleep_stage_label_history)
        if total <= 0:
            return {stage: 0.0 for stage in SLEEP_STAGE_ORDER}
        percentages = {}
        running = 0.0
        for stage in SLEEP_STAGE_ORDER[:-1]:
            pct = round((100.0 * counts.get(stage, 0)) / total, 1)
            percentages[stage] = pct
            running += pct
        percentages[SLEEP_STAGE_ORDER[-1]] = round(max(0.0, 100.0 - running), 1)
        return percentages

    def _update_derived(self, now: float = None, record_history: bool = True):
        """Compute HR and SpO2 from current buffers (called with lock held)."""
        if now is None:
            now = time.time()

        hr = -1.0
        for buf in (self.pat, self.oxi_b, self.oxi_a):
            if len(buf) >= WAVEFORM_RATE * 5:
                hr = _compute_heart_rate(np.array(buf), WAVEFORM_RATE)
                if hr > 0:
                    break
        self.current_hr = hr
        self.hr_history.append(hr if hr > 0 else float("nan"))

        spo2 = -1.0
        min_samples = WAVEFORM_RATE * 4
        if hr > 0 and len(self.oxi_a) >= min_samples and len(self.oxi_b) >= min_samples:
            a_arr = np.array(self.oxi_a)
            b_arr = np.array(self.oxi_b)

            tail = WAVEFORM_RATE * 4
            dc_a = float(np.mean(a_arr[-tail:]))
            dc_b = float(np.mean(b_arr[-tail:]))
            dc_ratio = (dc_a / dc_b) if (abs(dc_a) > 10 and abs(dc_b) > 10) else 1.0
            channels_distinct = abs(dc_ratio - 1.0) > 0.08

            if channels_distinct:
                spo2_ab, ratio_ab, spo2_ba, ratio_ba = _compute_spo2_dual(
                    a_arr, b_arr, hr, WAVEFORM_RATE)
                self._spo2_score_ab *= 0.9
                self._spo2_score_ba *= 0.9
                if spo2_ab > 0 and ratio_ab > 0 and 0.4 <= ratio_ab <= 1.3:
                    self._spo2_score_ab += 1.0 - abs(ratio_ab - 0.7)
                if spo2_ba > 0 and ratio_ba > 0 and 0.4 <= ratio_ba <= 1.3:
                    self._spo2_score_ba += 1.0 - abs(ratio_ba - 0.7)
                if self._spo2_score_ab >= self._spo2_score_ba:
                    spo2 = spo2_ab if spo2_ab > 0 else spo2_ba
                else:
                    spo2 = spo2_ba if spo2_ba > 0 else spo2_ab

        self._spo2_raw.append(spo2)
        valid_raw = sorted([v for v in self._spo2_raw if v > 0])
        if valid_raw:
            trim = max(1, len(valid_raw) // 5)
            trimmed = valid_raw[trim:-trim] if len(valid_raw) > 4 else valid_raw
            med = trimmed[len(trimmed) // 2]
            if self._spo2_ema < 0:
                self._spo2_ema = med
            else:
                self._spo2_ema += 0.15 * (med - self._spo2_ema)
            self.current_spo2 = self._spo2_ema
            self.spo2_history.append(self._spo2_ema)
        else:
            self.current_spo2 = -1.0
            self.spo2_history.append(float("nan"))

        elapsed = now - self.start_time if self.start_time else 0.0
        motion_level = _compute_motion_level(
            self.accel_x, self.accel_y, self.accel_z)
        if now - self._resp_cache_time >= 5.0:
            chest_arr = np.array(self.chest) if len(self.chest) >= WAVEFORM_RATE * 4 else np.array([])
            self._resp_cache = (
                _compute_resp_features(chest_arr, WAVEFORM_RATE)
                if len(chest_arr) else (-1.0, -1.0, -1.0)
            )
            self._resp_cache_time = now
        resp_rate, resp_amp, resp_variability = self._resp_cache
        pat_ratio = -1.0
        if self.current_spo2 > 0:
            if record_history:
                self.spo2_full_history.append(self.current_spo2)
                self.spo2_full_times.append(elapsed)
            if not self._desat_in_event:
                self._desat_baseline.append(self.current_spo2)
            if len(self._desat_baseline) >= 30:
                baseline = float(np.percentile(list(self._desat_baseline), 90))
                drop = baseline - self.current_spo2
                if not self._desat_in_event:
                    if drop >= 3.0:
                        self._desat_in_event = True
                        self._desat_start_elapsed = elapsed
                        self._desat_nadir = self.current_spo2
                else:
                    self._desat_nadir = min(self._desat_nadir, self.current_spo2)
                    if drop < 1.0:
                        if elapsed - self._desat_start_elapsed >= 10.0:
                            self.apnea_events.append(
                                (self._desat_start_elapsed, self._desat_nadir))
                            self.apnea_event_count += 1
                            pat_times = [ev[0] for ev in self.pat_events]
                            has_concurrent_pat = (
                                self._pat_in_event or
                                any(abs(t - self._desat_start_elapsed) <= 60.0
                                    for t in pat_times))
                            if not has_concurrent_pat:
                                self.central_events.append(
                                    (self._desat_start_elapsed,
                                     baseline - self._desat_nadir))
                                self.central_event_count += 1
                        self._desat_in_event = False

        if len(self.pat) >= WAVEFORM_RATE * 3:
            pat_arr = np.array(self.pat)
            env = float(np.ptp(pat_arr[-WAVEFORM_RATE * 3:]))
            if env > 0:
                if not self._pat_in_event:
                    self._pat_baseline_buf.append(env)
                if len(self._pat_baseline_buf) >= 30:
                    pat_baseline = float(
                        np.percentile(list(self._pat_baseline_buf), 75))
                    ratio = env / pat_baseline if pat_baseline > 0 else 1.0
                    pat_ratio = ratio * 100.0
                    if record_history:
                        self.pat_amp_history.append(pat_ratio)
                        self.pat_amp_times.append(elapsed)
                    if not self._pat_in_event:
                        if ratio <= 0.70:
                            self._pat_in_event = True
                            self._pat_event_start = elapsed
                            self._pat_hr_baseline = self.current_hr
                    else:
                        recovered = ratio > 0.80
                        timed_out = elapsed - self._pat_event_start > 120.0
                        if recovered or timed_out:
                            duration = elapsed - self._pat_event_start
                            if 10.0 <= duration <= 120.0:
                                hr_rise = (
                                    self.current_hr - self._pat_hr_baseline
                                    if self._pat_hr_baseline > 0
                                    and self.current_hr > 0 else 0.0)
                                n_look = min(int(duration) + 5,
                                             len(self.spo2_full_history))
                                spo2_drop = 0.0
                                if n_look >= 2:
                                    recent = list(
                                        self.spo2_full_history)[-n_look:]
                                    valid = [v for v in recent if v > 0]
                                    if valid:
                                        spo2_drop = max(
                                            0.0, max(valid) - min(valid))
                                if spo2_drop >= 4.0:
                                    evt_type = EVT_APNEA
                                elif spo2_drop >= 3.0:
                                    evt_type = EVT_HYPOPNEA
                                elif hr_rise >= 6.0:
                                    evt_type = EVT_RERA
                                else:
                                    evt_type = EVT_PAT
                                self.pat_events.append(
                                    (self._pat_event_start, ratio,
                                     hr_rise, spo2_drop, evt_type))
                                self.pat_event_type_counts[evt_type] += 1
                            self._pat_in_event = False

        if elapsed >= 60.0 and self.start_time:
            hours = elapsed / 3600.0
            if self.pat_event_type_counts:
                n_apnea = self.pat_event_type_counts.get(EVT_APNEA, 0)
                n_hyp   = self.pat_event_type_counts.get(EVT_HYPOPNEA, 0)
                n_rera  = self.pat_event_type_counts.get(EVT_RERA, 0)
            else:
                n_apnea = sum(1 for ev in self.pat_events if ev[4] == EVT_APNEA)
                n_hyp   = sum(1 for ev in self.pat_events if ev[4] == EVT_HYPOPNEA)
                n_rera  = sum(1 for ev in self.pat_events if ev[4] == EVT_RERA)
            apnea_count = self.apnea_event_count if self.apnea_event_count > 0 else len(self.apnea_events)
            self.ahi_estimate  = apnea_count / hours
            self.pahi_estimate = (n_apnea + n_hyp) / hours
            self.rdi_estimate  = (n_apnea + n_hyp + n_rera) / hours

        if record_history:
            self.hr_full_history.append(hr if hr > 0 else float("nan"))
            self.hr_full_times.append(elapsed)
            self.motion_level_history.append(
                motion_level if motion_level >= 0 else float("nan"))
            self.motion_level_times.append(elapsed)
            self.resp_rate_history.append(resp_rate if resp_rate > 0 else float("nan"))
            self.resp_amp_history.append(resp_amp if resp_amp > 0 else float("nan"))
            self.resp_variability_history.append(
                resp_variability if resp_variability >= 0 else float("nan"))
            stage = self._estimate_sleep_stage(
                motion_level=motion_level,
                resp_variability=resp_variability,
                pat_ratio=pat_ratio,
                elapsed=elapsed,
            )
            self.current_sleep_stage = stage
            self.sleep_stage_label_history.append(stage)
            self.sleep_stage_history.append(SLEEP_STAGE_LEVELS[stage])
            self.sleep_stage_times.append(elapsed)
            self.sleep_stage_counts[stage] += 1
            self.sleep_stage_total += 1

    def _waveform_buf(self, name: str):
        return {"OxiA": self.oxi_a, "OxiB": self.oxi_b,
                "PAT": self.pat, "Chest": self.chest}.get(name)

    def clone(self, compact: bool = False):
        other = SensorBuffers()
        with self.lock:
            recent_seconds = 600
            recent_stage = 120
            for name in (
                "oxi_a", "oxi_b", "pat", "chest",
                "accel_x", "accel_y", "accel_z", "field_a", "field_b",
                "hr_history", "spo2_history", "events",
                "spo2_full_history", "spo2_full_times",
                "_desat_baseline",
                "pat_amp_history", "pat_amp_times", "_pat_baseline_buf",
                "_spo2_raw",
                "hr_full_history", "hr_full_times",
                "motion_level_history", "motion_level_times",
                "resp_rate_history", "resp_amp_history",
                "resp_variability_history",
                "sleep_stage_history", "sleep_stage_times",
                "sleep_stage_label_history",
            ):
                values = getattr(self, name)
                copied = values
                if compact:
                    if name in {
                        "spo2_full_history", "spo2_full_times",
                        "pat_amp_history", "pat_amp_times",
                        "hr_full_history", "hr_full_times",
                        "motion_level_history", "motion_level_times",
                        "resp_rate_history", "resp_amp_history",
                        "resp_variability_history",
                    }:
                        copied = list(values)[-recent_seconds:]
                    elif name in {
                        "sleep_stage_history", "sleep_stage_times",
                        "sleep_stage_label_history",
                    }:
                        copied = list(values)[-recent_stage:]
                setattr(other, name, deque(copied, maxlen=values.maxlen))
            other.current_hr = self.current_hr
            other.current_spo2 = self.current_spo2
            other._last_derive_time = self._last_derive_time
            other._spo2_score_ab = self._spo2_score_ab
            other._spo2_score_ba = self._spo2_score_ba
            other._spo2_ema = self._spo2_ema
            other.current_sleep_stage = self.current_sleep_stage
            other.body_position = self.body_position
            other.body_position_label = self.body_position_label
            other.metric_value = self.metric_value
            other.packet_count = self.packet_count
            other.total_bytes = self.total_bytes
            other.start_time = self.start_time
            other.last_motion_crc_ok = self.last_motion_crc_ok
            other.last_motion_crc_total = self.last_motion_crc_total
            other.apnea_events = list(self.apnea_events if not compact else [])
            other.apnea_event_count = self.apnea_event_count
            other.ahi_estimate = self.ahi_estimate
            other._desat_in_event = self._desat_in_event
            other._desat_start_elapsed = self._desat_start_elapsed
            other._desat_nadir = self._desat_nadir
            other.pat_events = list(self.pat_events if not compact else [])
            other.pat_event_type_counts = Counter(self.pat_event_type_counts)
            other.pahi_estimate = self.pahi_estimate
            other._pat_in_event = self._pat_in_event
            other._pat_event_start = self._pat_event_start
            other._pat_hr_baseline = self._pat_hr_baseline
            other.central_events = list(self.central_events if not compact else [])
            other.central_event_count = self.central_event_count
            other.rdi_estimate = self.rdi_estimate
            other.sleep_stage_counts = Counter(self.sleep_stage_counts)
            other.sleep_stage_total = self.sleep_stage_total
        return other

    def serialize_state(self) -> dict:
        with self.lock:
            state = {}
            for name in (
                "oxi_a", "oxi_b", "pat", "chest",
                "accel_x", "accel_y", "accel_z", "field_a", "field_b",
                "hr_history", "spo2_history", "events",
                "spo2_full_history", "spo2_full_times",
                "_desat_baseline",
                "pat_amp_history", "pat_amp_times", "_pat_baseline_buf",
                "_spo2_raw",
                "hr_full_history", "hr_full_times",
                "motion_level_history", "motion_level_times",
                "resp_rate_history", "resp_amp_history",
                "resp_variability_history",
                "sleep_stage_history", "sleep_stage_times",
                "sleep_stage_label_history",
            ):
                values = getattr(self, name)
                state[name] = {"items": list(values), "maxlen": values.maxlen}
            state.update({
                "current_hr": self.current_hr,
                "current_spo2": self.current_spo2,
                "_last_derive_time": self._last_derive_time,
                "_spo2_score_ab": self._spo2_score_ab,
                "_spo2_score_ba": self._spo2_score_ba,
                "_spo2_ema": self._spo2_ema,
                "current_sleep_stage": self.current_sleep_stage,
                "body_position": self.body_position,
                "body_position_label": self.body_position_label,
                "metric_value": self.metric_value,
                "packet_count": self.packet_count,
                "total_bytes": self.total_bytes,
                "start_time": self.start_time,
                "last_motion_crc_ok": self.last_motion_crc_ok,
                "last_motion_crc_total": self.last_motion_crc_total,
                "apnea_events": list(self.apnea_events),
                "apnea_event_count": self.apnea_event_count,
                "ahi_estimate": self.ahi_estimate,
                "_desat_in_event": self._desat_in_event,
                "_desat_start_elapsed": self._desat_start_elapsed,
                "_desat_nadir": self._desat_nadir,
                "pat_events": list(self.pat_events),
                "pat_event_type_counts": dict(self.pat_event_type_counts),
                "pahi_estimate": self.pahi_estimate,
                "_pat_in_event": self._pat_in_event,
                "_pat_event_start": self._pat_event_start,
                "_pat_hr_baseline": self._pat_hr_baseline,
                "central_events": list(self.central_events),
                "central_event_count": self.central_event_count,
                "rdi_estimate": self.rdi_estimate,
                "sleep_stage_counts": dict(self.sleep_stage_counts),
                "sleep_stage_total": self.sleep_stage_total,
            })
            return state

    @classmethod
    def from_serialized_state(cls, state: dict) -> "SensorBuffers":
        other = cls()
        with other.lock:
            for name in (
                "oxi_a", "oxi_b", "pat", "chest",
                "accel_x", "accel_y", "accel_z", "field_a", "field_b",
                "hr_history", "spo2_history", "events",
                "spo2_full_history", "spo2_full_times",
                "_desat_baseline",
                "pat_amp_history", "pat_amp_times", "_pat_baseline_buf",
                "_spo2_raw",
                "hr_full_history", "hr_full_times",
                "motion_level_history", "motion_level_times",
                "resp_rate_history", "resp_amp_history",
                "resp_variability_history",
                "sleep_stage_history", "sleep_stage_times",
                "sleep_stage_label_history",
            ):
                payload = state[name]
                setattr(other, name, deque(payload["items"], maxlen=payload["maxlen"]))
            for name in (
                "current_hr", "current_spo2", "_last_derive_time",
                "_spo2_score_ab", "_spo2_score_ba", "_spo2_ema",
                "current_sleep_stage", "body_position", "body_position_label",
                "metric_value", "packet_count", "total_bytes", "start_time",
                "last_motion_crc_ok", "last_motion_crc_total",
                "apnea_events", "apnea_event_count", "ahi_estimate",
                "_desat_in_event", "_desat_start_elapsed", "_desat_nadir",
                "pat_events", "pahi_estimate", "_pat_in_event",
                "_pat_event_start", "_pat_hr_baseline", "central_events",
                "central_event_count", "rdi_estimate", "sleep_stage_total",
            ):
                setattr(other, name, state[name])
            other.pat_event_type_counts = Counter(state["pat_event_type_counts"])
            other.sleep_stage_counts = Counter(state["sleep_stage_counts"])
        return other

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
                "spo2_full_history": (np.array(self.spo2_full_history)
                                      if self.spo2_full_history else np.array([])),
                "spo2_full_times": (np.array(self.spo2_full_times)
                                    if self.spo2_full_times else np.array([])),
                "apnea_events": list(self.apnea_events),
                "apnea_event_count": self.apnea_event_count,
                "ahi_estimate": self.ahi_estimate,
                "pat_amp_history": (np.array(self.pat_amp_history)
                                    if self.pat_amp_history else np.array([])),
                "pat_amp_times": (np.array(self.pat_amp_times)
                                  if self.pat_amp_times else np.array([])),
                "pat_events": list(self.pat_events),
                "pat_event_counts": dict(self.pat_event_type_counts),
                "pahi_estimate": self.pahi_estimate,
                "rdi_estimate": self.rdi_estimate,
                "central_events": list(self.central_events),
                "central_event_count": self.central_event_count,
                "hr_full_history": (np.array(self.hr_full_history)
                                    if self.hr_full_history else np.array([])),
                "hr_full_times": (np.array(self.hr_full_times)
                                  if self.hr_full_times else np.array([])),
                "motion_level_history": (np.array(self.motion_level_history)
                                         if self.motion_level_history else np.array([])),
                "motion_level_times": (np.array(self.motion_level_times)
                                       if self.motion_level_times else np.array([])),
                "resp_rate_history": (np.array(self.resp_rate_history)
                                      if self.resp_rate_history else np.array([])),
                "resp_amp_history": (np.array(self.resp_amp_history)
                                     if self.resp_amp_history else np.array([])),
                "resp_variability_history": (
                    np.array(self.resp_variability_history)
                    if self.resp_variability_history else np.array([])),
                "sleep_stage_history": (np.array(self.sleep_stage_history)
                                        if self.sleep_stage_history else np.array([])),
                "sleep_stage_times": (np.array(self.sleep_stage_times)
                                      if self.sleep_stage_times else np.array([])),
                "sleep_stage_labels": list(self.sleep_stage_label_history),
                "sleep_stage_percentages": self.sleep_stage_percentages(),
                "current_sleep_stage": self.current_sleep_stage,
            }
