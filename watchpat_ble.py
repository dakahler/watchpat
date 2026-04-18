"""
WatchPAT ONE BLE Client
=======================
Connects to a WatchPAT ONE device over Bluetooth Low Energy using the
Nordic UART Service (NUS) protocol and exposes sensor data.

Protocol reverse-engineered from WatchPAT Android app v4.2.0.

Usage:
    python watchpat_ble.py [--serial SERIAL] [--scan-only] [--tech-status] [--monitor]
"""

import argparse
import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from io import BytesIO
from typing import Optional, Callable

from watchpat_protocol import (
    WatchpatPacket,
    KaitaiStream,
    BytesIO as KaiBytesIO,
    ValidationNotEqualError,
    parse_data_payload,
    crc16_watchpat,
    verify_crc,
    decode_byte_delta_waveform as _decode_byte_delta,
    decode_nibble_delta_waveform as _decode_nibble_delta,
    motion_subframe_crc_valid,
)

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

logger = logging.getLogger("watchpat")

# ---------------------------------------------------------------------------
# BLE UUIDs – Nordic UART Service
# ---------------------------------------------------------------------------
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write to device
NUS_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify from device
CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------
PACKET_SIGNATURE = 0xBBBB  # -17477 as signed short = 0xBBBB unsigned
HEADER_SIZE = 24
MAX_BLE_CHUNK = 20


class Opcode(IntEnum):
    """Command opcodes (sent to device)."""
    ACK = 0x0000
    SESSION_START = 0x0100
    START_ACQUISITION = 0x0600
    STOP_ACQUISITION = 0x0700
    RESET_DEVICE = 0x0B00
    SET_PARAMETERS_FILE = 0x0C00
    GET_PARAMETERS_FILE = 0x0D00
    SEND_STORED_DATA = 0x1000
    BIT_REQUEST = 0x1200
    TECH_STATUS_REQUEST = 0x1500
    GET_EEPROM = 0x1D00
    SET_EEPROM = 0x1F00
    SET_LEDS = 0x2300
    SET_DEVICE_SERIAL = 0x2400
    START_FINGER_DETECTION = 0x2500
    CLEAR_DATA = 0x2700
    IS_DEVICE_PAIRED = 0x2A00
    FW_UPGRADE_REQUEST = 0x3000
    RESET_REASON = 0x3900
    GET_LOG_FILE = 0x4400
    SET_NIGHTS_COUNTER = 0x4600


class ResponseOpcode(IntEnum):
    """Response opcodes (received from device)."""
    ACK = 0x0000
    SESSION_CONFIRM = 0x0200
    CONFIG_RESPONSE = 0x0500
    DATA_PACKET = 0x0800
    END_OF_TEST = 0x0900
    ERROR_STATUS = 0x0A00
    PARAMETERS_FILE = 0x0E00
    BIT_RESPONSE = 0x1300
    TECH_STATUS = 0x1600
    AFE_REGISTERS = 0x1800
    ACTIGRAPH_REGISTERS = 0x1B00
    EEPROM_VALUES = 0x1E00
    IS_PAIRED_RESPONSE = 0x2B00
    FW_UPGRADE_RESPONSE = 0x3100
    FINGER_TEST = 0x2600
    RESET_REASON_RESPONSE = 0x3A00
    LOG_FILE_RESPONSE = 0x4500


class AckStatus(IntEnum):
    OK = 0
    CRC_ERROR = 1
    ILLEGAL_OPCODE = 2
    NON_UNIQUE_ID = 3
    INVALID_PARAM = 4


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class TechStatus:
    """Technical status / sensor readings from the device."""
    battery_voltage: int = 0
    vdd_voltage: int = 0
    ir_led: int = 0
    red_led: int = 0
    pat_led: int = 0

    def __str__(self):
        return (
            f"Battery: {self.battery_voltage} mV | "
            f"VDD: {self.vdd_voltage} mV | "
            f"IR LED: {self.ir_led} | "
            f"Red LED: {self.red_led} | "
            f"PAT LED: {self.pat_led}"
        )


@dataclass
class DeviceConfig:
    """Device configuration received after session start."""
    hw_major: int = 0
    hw_minor: int = 0
    fw_version: int = 0
    serial_number: int = 0
    device_subtype: int = 0
    is_wcp_less: bool = False
    is_wp1m: bool = False
    has_finger_detection: bool = False
    pin_code: str = ""
    raw: bytes = b""

    def __str__(self):
        return (
            f"SN: {self.serial_number} | "
            f"HW: {self.hw_major}.{self.hw_minor} | "
            f"FW: {self.fw_version} | "
            f"WCPLess: {self.is_wcp_less} | "
            f"WP1M: {self.is_wp1m} | "
            f"FingerDet: {self.has_finger_detection}"
        )


@dataclass
class BITResult:
    """Built-In Test result bit flags."""
    raw_value: int = 0

    @property
    def battery_depleted(self) -> bool:
        return bool(self.raw_value & 0x01)

    @property
    def battery_low(self) -> bool:
        return bool(self.raw_value & 0x02)

    @property
    def actigraph_error(self) -> bool:
        return bool(self.raw_value & 0x04)

    @property
    def naf_error(self) -> bool:
        return bool(self.raw_value & 0x08)

    @property
    def vdd_error(self) -> bool:
        return bool(self.raw_value & 0x10)

    @property
    def used_device(self) -> bool:
        return bool(self.raw_value & 0x20)

    @property
    def flash_error(self) -> bool:
        return bool(self.raw_value & 0x40)

    @property
    def probe_led_error(self) -> bool:
        return bool(self.raw_value & 0x80)

    @property
    def probe_photo_error(self) -> bool:
        return bool(self.raw_value & 0x100)

    @property
    def probe_failure(self) -> bool:
        return bool(self.raw_value & 0x200)

    @property
    def spb_error(self) -> bool:
        return bool(self.raw_value & 0x400)

    def __str__(self):
        flags = []
        if self.raw_value == 0:
            return "BIT: OK (no errors)"
        if self.battery_depleted:
            flags.append("BATTERY_DEPLETED")
        if self.battery_low:
            flags.append("BATTERY_LOW")
        if self.actigraph_error:
            flags.append("ACTIGRAPH_ERR")
        if self.naf_error:
            flags.append("NAF_ERR")
        if self.vdd_error:
            flags.append("VDD_ERR")
        if self.used_device:
            flags.append("USED_DEVICE")
        if self.flash_error:
            flags.append("FLASH_ERR")
        if self.probe_led_error:
            flags.append("PROBE_LED_ERR")
        if self.probe_photo_error:
            flags.append("PROBE_PHOTO_ERR")
        if self.probe_failure:
            flags.append("PROBE_FAILURE")
        if self.spb_error:
            flags.append("SPB_ERR")
        return f"BIT: {' | '.join(flags)} (0x{self.raw_value:04x})"


# ---------------------------------------------------------------------------
# Sensor channel identifiers (record_id/record_type pairs)
# ---------------------------------------------------------------------------
class RecordKind(IntEnum):
    """Logical record types found inside DATA_PACKET payloads."""
    WAVEFORM_01_11 = 0x0111  # Oximetry channel A (red or IR)
    WAVEFORM_02_11 = 0x0211  # Oximetry channel B (red or IR)
    WAVEFORM_03_11 = 0x0311  # PAT waveform
    WAVEFORM_04_01 = 0x0401  # Chest sensor / respiratory effort
    METRIC_05_10 = 0x0510    # Once-per-second derived metric
    MOTION_06_00 = 0x0600    # SBP chest sensor motion/orientation (5 Hz)
    EVENT_0C_00 = 0x0C00     # Event code
    EVENT_0D_00 = 0x0D00     # Event payload (20 bytes)

    @classmethod
    def from_id_type(cls, record_id: int, record_type: int) -> Optional["RecordKind"]:
        key = (record_id << 8) | record_type
        try:
            return cls(key)
        except ValueError:
            return None


RECORD_SYNC = 0xAAAA
RECORD_HEADER_SIZE = 12


@dataclass
class LogicalRecordHeader:
    """12-byte header for each logical record inside a DATA_PACKET."""
    record_id: int = 0
    record_type: int = 0
    payload_len: int = 0
    rate: int = 0
    flags: int = 0
    kind: Optional[RecordKind] = None


@dataclass
class DecodedWaveform:
    """Decoded waveform samples from a byte-delta or nibble-delta record."""
    kind: RecordKind = RecordKind.WAVEFORM_01_11
    seed: int = 0
    samples: list = field(default_factory=list)
    rate: int = 100
    flags: int = 0
    raw_payload: bytes = b""

    @property
    def channel_name(self) -> str:
        names = {
            RecordKind.WAVEFORM_01_11: "OxiA",
            RecordKind.WAVEFORM_02_11: "OxiB",
            RecordKind.WAVEFORM_03_11: "PAT",
            RecordKind.WAVEFORM_04_01: "Chest",
        }
        return names.get(self.kind, f"Wave{self.kind:04x}")


@dataclass
class MotionSubframe:
    """One of five 16-byte subframes inside a 06/00 record."""
    field_a: int = 0    # Motion/chest summary
    field_b: int = 0    # Snore summary candidate
    x: int = 0          # Accel X (signed)
    y: int = 0          # Accel Y (signed)
    z: int = 0          # Accel Z (signed)
    crc_valid: bool = False


@dataclass
class MotionRecord:
    """Decoded 06/00 motion summary (5 subframes per second)."""
    subframes: list = field(default_factory=list)  # list[MotionSubframe]
    rate: int = 5
    flags: int = 0
    raw_payload: bytes = b""

    @property
    def body_position(self) -> str:
        """Dominant-axis body position from latest subframe."""
        if not self.subframes:
            return "?"
        sf = self.subframes[-1]
        axes = [("x+", sf.x), ("x-", -sf.x), ("y+", sf.y),
                ("y-", -sf.y), ("z+", sf.z), ("z-", -sf.z)]
        label, _ = max(axes, key=lambda a: a[1])
        return label


@dataclass
class MetricRecord:
    """Decoded 05/10 once-per-second metric."""
    value: int = 0
    rate: int = 1
    flags: int = 0


@dataclass
class EventRecord:
    """Decoded event record (0C/00 or 0D/00)."""
    kind: RecordKind = RecordKind.EVENT_0C_00
    value: int = 0
    payload: bytes = b""


@dataclass
class ParsedDataPacket:
    """All logical records decoded from one DATA_PACKET payload."""
    index: int = 0
    waveforms: list = field(default_factory=list)   # list[DecodedWaveform]
    motion: Optional[MotionRecord] = None
    metric: Optional[MetricRecord] = None
    events: list = field(default_factory=list)       # list[EventRecord]
    raw_payload: bytes = b""


# ---------------------------------------------------------------------------
# Logical record parsing & waveform decoding
# (crc16_watchpat and verify_crc imported from watchpat_protocol)
# ---------------------------------------------------------------------------

def decode_byte_delta_waveform(payload: bytes, record_hdr: LogicalRecordHeader) -> DecodedWaveform:
    """Decode 01/11, 02/11, or 03/11 waveform records into a DecodedWaveform."""
    samples = _decode_byte_delta(payload)
    wf = DecodedWaveform(
        kind=record_hdr.kind,
        rate=record_hdr.rate,
        flags=record_hdr.flags,
        raw_payload=bytes(payload),
        seed=samples[0] if samples else 0,
        samples=samples,
    )
    return wf


def decode_nibble_delta_waveform(payload: bytes, record_hdr: LogicalRecordHeader) -> DecodedWaveform:
    """Decode 04/01 chest-sensor waveform into a DecodedWaveform."""
    samples = _decode_nibble_delta(payload)
    wf = DecodedWaveform(
        kind=record_hdr.kind,
        rate=record_hdr.rate,
        flags=record_hdr.flags,
        raw_payload=bytes(payload),
        seed=samples[0] if samples else 0,
        samples=samples,
    )
    return wf


def decode_metric_record(payload: bytes, record_hdr: LogicalRecordHeader) -> MetricRecord:
    """Decode 05/10 — a 4-byte little-endian signed int."""
    value = 0
    if len(payload) >= 4:
        value = struct.unpack_from("<i", payload, 0)[0]
    return MetricRecord(value=value, rate=record_hdr.rate, flags=record_hdr.flags)


def decode_event_record(payload: bytes, record_hdr: LogicalRecordHeader) -> EventRecord:
    """Decode 0C/00 (2-byte event code) or 0D/00 (20-byte event payload)."""
    evt = EventRecord(kind=record_hdr.kind, payload=bytes(payload))
    if record_hdr.kind == RecordKind.EVENT_0C_00 and len(payload) >= 2:
        evt.value = struct.unpack_from("<H", payload, 0)[0]
    return evt


def parse_logical_records(data: bytes) -> list:
    """Parse all logical records from a DATA_PACKET payload via Kaitai."""
    try:
        dp = parse_data_payload(data)
    except Exception as e:
        logger.debug("parse_logical_records: %s", e)
        return []

    records = []
    for rec in dp.records:
        kind_val = (rec.record_id << 8) | rec.record_type
        kind = RecordKind.from_id_type(rec.record_id, rec.record_type)
        hdr = LogicalRecordHeader(
            record_id=rec.record_id,
            record_type=rec.record_type,
            payload_len=rec.payload_len,
            rate=rec.rate,
            flags=rec.flags,
            kind=kind,
        )
        raw = rec._raw_payload

        if kind_val in (RecordKind.WAVEFORM_01_11, RecordKind.WAVEFORM_02_11,
                         RecordKind.WAVEFORM_03_11):
            records.append(("waveform", hdr, decode_byte_delta_waveform(raw, hdr)))
        elif kind_val == RecordKind.WAVEFORM_04_01:
            records.append(("waveform", hdr, decode_nibble_delta_waveform(raw, hdr)))
        elif kind_val == RecordKind.METRIC_05_10:
            records.append(("metric", hdr, decode_metric_record(raw, hdr)))
        elif kind_val == RecordKind.MOTION_06_00:
            mr = MotionRecord(rate=hdr.rate, flags=hdr.flags, raw_payload=raw)
            for sf in rec.payload.subframes:
                mr.subframes.append(MotionSubframe(
                    field_a=sf.field_a, field_b=sf.field_b,
                    x=sf.x, y=sf.y, z=sf.z,
                    crc_valid=motion_subframe_crc_valid(sf),
                ))
            records.append(("motion", hdr, mr))
        elif kind_val in (RecordKind.EVENT_0C_00, RecordKind.EVENT_0D_00):
            records.append(("event", hdr, decode_event_record(raw, hdr)))
        else:
            records.append(("unknown", hdr, raw))

    return records


def parse_data_packet(payload: bytes, index: int = 0) -> ParsedDataPacket:
    """Parse a full DATA_PACKET payload into structured decoded records."""
    pkt = ParsedDataPacket(index=index, raw_payload=bytes(payload))
    for tag, hdr, decoded in parse_logical_records(payload):
        if tag == "waveform":
            pkt.waveforms.append(decoded)
        elif tag == "motion":
            pkt.motion = decoded
        elif tag == "metric":
            pkt.metric = decoded
        elif tag == "event":
            pkt.events.append(decoded)
    return pkt


# ---------------------------------------------------------------------------
# Packet building / parsing
# ---------------------------------------------------------------------------
_packet_counter = 0


def _next_packet_id() -> int:
    global _packet_counter
    _packet_counter += 1
    return _packet_counter


def build_header(opcode: int, packet_id: int, total_len: int,
                 opcode_dependent: int = 0) -> bytearray:
    """Build the 24-byte packet header.

    Java uses ByteBuffer (big-endian) but applies Integer.reverseBytes() /
    Short.reverseBytes() to numeric fields before writing, so the net wire
    format is: signature & opcode in big-endian, everything else little-endian.
    """
    hdr = bytearray(HEADER_SIZE)
    struct.pack_into(">H", hdr, 0, 0xBBBB)            # signature (big-endian)
    struct.pack_into(">H", hdr, 2, opcode)             # opcode (big-endian)
    struct.pack_into("<Q", hdr, 4, 0)                  # timestamp (little-endian)
    struct.pack_into("<I", hdr, 12, packet_id)         # packet ID (little-endian)
    struct.pack_into("<H", hdr, 16, total_len)         # length (little-endian)
    struct.pack_into("<H", hdr, 18, opcode_dependent)  # opcode-dependent (LE)
    struct.pack_into("<H", hdr, 20, 0)                 # reserved
    struct.pack_into("<H", hdr, 22, 0)                 # CRC placeholder
    return hdr


def finalize_packet(packet: bytearray) -> bytearray:
    """Compute and insert CRC into packet bytes 22-23."""
    # Zero out CRC field before computing
    packet[22] = 0
    packet[23] = 0
    crc = crc16_watchpat(bytes(packet))
    struct.pack_into("<H", packet, 22, crc)
    return packet


def chunk_packet(packet: bytes) -> list[bytes]:
    """Split a packet into 20-byte BLE chunks."""
    chunks = []
    for i in range(0, len(packet), MAX_BLE_CHUNK):
        chunks.append(packet[i:i + MAX_BLE_CHUNK])
    return chunks


def build_command(opcode: int, payload: bytes = b"",
                  opcode_dependent: int = 0) -> list[bytes]:
    """Build a complete command packet, finalize CRC, and chunk it."""
    pid = _next_packet_id()
    total_len = HEADER_SIZE + len(payload)
    hdr = build_header(opcode, pid, total_len, opcode_dependent)
    packet = bytearray(hdr) + bytearray(payload)
    packet = finalize_packet(packet)
    return chunk_packet(bytes(packet)), pid


def build_ack(response_opcode: int, status: int, response_id: int) -> list[bytes]:
    """Build an ACK packet for a received response."""
    pid = response_id
    payload = struct.pack(">Hb", response_opcode, status) + b"\x00\x00"
    total_len = HEADER_SIZE + len(payload)
    hdr = build_header(Opcode.ACK, pid, total_len)
    packet = bytearray(hdr) + bytearray(payload)
    packet = finalize_packet(packet)
    return chunk_packet(bytes(packet))


def build_is_device_paired() -> tuple[list[bytes], int]:
    return build_command(Opcode.IS_DEVICE_PAIRED)


def build_tech_status_request() -> tuple[list[bytes], int]:
    return build_command(Opcode.TECH_STATUS_REQUEST)


def build_bit_request(test_flags: int = 0) -> tuple[list[bytes], int]:
    payload = struct.pack(">I", test_flags)
    return build_command(Opcode.BIT_REQUEST, payload)


def build_start_finger_detection() -> tuple[list[bytes], int]:
    return build_command(Opcode.START_FINGER_DETECTION)


def build_session_start(mobile_id: int = 0, mode: int = 1,
                        os_version: bytes = b"14") -> tuple[list[bytes], int]:
    """Build SessionStart command. mode: 1=normal, 4=resume."""
    ts = int(time.time())
    payload = struct.pack(">I", mobile_id)
    payload += struct.pack("b", mode)
    payload += os_version[:14].ljust(14, b"\x00")
    payload += b"\x00"
    return build_command(Opcode.SESSION_START, payload)


def build_reset_reason() -> tuple[list[bytes], int]:
    return build_command(Opcode.RESET_REASON)


def build_start_acquisition() -> tuple[list[bytes], int]:
    return build_command(Opcode.START_ACQUISITION)


def build_stop_acquisition() -> tuple[list[bytes], int]:
    return build_command(Opcode.STOP_ACQUISITION)


def build_set_leds(led_mask: int) -> tuple[list[bytes], int]:
    return build_command(Opcode.SET_LEDS, struct.pack("b", led_mask))


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def parse_header(data: bytes) -> dict:
    """Parse a 24-byte packet header."""
    if len(data) < HEADER_SIZE:
        return None
    sig = struct.unpack_from(">H", data, 0)[0]
    opcode = struct.unpack_from(">H", data, 2)[0]
    timestamp = struct.unpack_from("<Q", data, 4)[0]
    packet_id = struct.unpack_from("<I", data, 12)[0]
    length = struct.unpack_from("<H", data, 16)[0]
    opcode_dep = struct.unpack_from("<I", data, 18)[0]
    crc = struct.unpack_from("<H", data, 22)[0]
    return {
        "signature": sig,
        "opcode": opcode,
        "timestamp": timestamp,
        "packet_id": packet_id,
        "length": length,
        "opcode_dependent": opcode_dep,
        "crc": crc,
    }


def parse_tech_status(payload: bytes) -> TechStatus:
    """Parse technical status payload via Kaitai TechStatusPayload."""
    ts = TechStatus()
    try:
        tp = WatchpatPacket.TechStatusPayload(
            KaitaiStream(KaiBytesIO(payload)), None, None
        )
        ts.battery_voltage = tp.battery_voltage
        ts.vdd_voltage = tp.vdd_voltage
        ts.ir_led = tp.ir_led
        ts.red_led = tp.red_led
        ts.pat_led = tp.pat_led
    except Exception:
        pass
    return ts


def parse_device_config(payload: bytes) -> DeviceConfig:
    """Parse device configuration via Kaitai SessionConfirmPayload."""
    cfg = DeviceConfig()
    cfg.raw = bytes(payload)
    try:
        scp = WatchpatPacket.SessionConfirmPayload(
            KaitaiStream(KaiBytesIO(payload)), None, None
        )
        cfg.hw_major = scp.hw_major or 0
        cfg.hw_minor = scp.hw_minor or 0
        cfg.fw_version = scp.fw_version or 0
        if scp.serial_number is not None:
            cfg.serial_number = scp.serial_number
        if scp.pin_code_raw is not None:
            cfg.pin_code = f"{scp.pin_code_raw:04d}"
        if scp.device_subtype is not None:
            cfg.device_subtype = scp.device_subtype
            cfg.is_wcp_less = scp.is_wcp_less or False
            cfg.is_wp1m = scp.is_wp1m or False
            cfg.has_finger_detection = scp.has_finger_detection or False
    except Exception:
        pass
    return cfg


def parse_bit_response(payload: bytes) -> BITResult:
    """Parse BIT response via Kaitai BitResponsePayload."""
    try:
        bp = WatchpatPacket.BitResponsePayload(
            KaitaiStream(KaiBytesIO(payload)), None, None
        )
        return BITResult(raw_value=bp.raw_flags)
    except Exception:
        return BITResult()


# ---------------------------------------------------------------------------
# Reassembly state machine
# ---------------------------------------------------------------------------
class PacketReassembler:
    """Reassembles BLE chunks into complete WatchPAT packets."""

    def __init__(self):
        self.buffer = BytesIO()
        self.expected_len = 0
        self.state = 0  # 0=waiting_header, 1=collecting, 2=complete

    def reset(self):
        self.buffer = BytesIO()
        self.expected_len = 0
        self.state = 0

    def feed(self, chunk: bytes) -> Optional[bytes]:
        """Feed a BLE notification chunk. Returns complete packet or None."""
        if self.state == 0:
            # Expect the start of a new packet
            if len(chunk) < 2:
                return None
            sig = struct.unpack_from(">H", chunk, 0)[0]
            if sig != 0xBBBB:
                logger.warning("Bad signature: 0x%04x, discarding", sig)
                self.reset()
                return None
            if len(chunk) < 18:
                # Need at least bytes 16-17 for length
                self.buffer.write(chunk)
                self.state = 1
                return None
            self.expected_len = struct.unpack_from("<H", chunk, 16)[0]
            if self.expected_len <= 0:
                logger.warning("Zero-length packet, discarding")
                self.reset()
                return None
            self.state = 1

        self.buffer.write(chunk)
        current_len = self.buffer.tell()

        # Try to read expected_len from buffer if we didn't get it yet
        if self.expected_len == 0 and current_len >= 18:
            pos = self.buffer.tell()
            self.buffer.seek(16)
            self.expected_len = struct.unpack("<H", self.buffer.read(2))[0]
            self.buffer.seek(pos)

        if self.expected_len > 0 and current_len >= self.expected_len:
            data = self.buffer.getvalue()[:self.expected_len]
            self.reset()
            return data

        return None


# ---------------------------------------------------------------------------
# WatchPAT BLE Client
# ---------------------------------------------------------------------------
class WatchPATClient:
    """High-level async client for WatchPAT ONE devices."""

    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.device: Optional[BLEDevice] = None
        self.reassembler = PacketReassembler()
        self.pending_responses: dict[int, asyncio.Future] = {}
        self.config: Optional[DeviceConfig] = None
        self.connected = False

        # Callbacks for data events
        self.on_tech_status: Optional[Callable[[TechStatus], None]] = None
        self.on_data_packet: Optional[Callable[[bytes], None]] = None
        self.on_parsed_data: Optional[Callable[[ParsedDataPacket], None]] = None
        self.on_config: Optional[Callable[[DeviceConfig], None]] = None
        self.on_bit_result: Optional[Callable[[BITResult], None]] = None
        self.on_finger_test: Optional[Callable[[int], None]] = None
        self.on_error: Optional[Callable[[int], None]] = None
        self.on_end_of_test: Optional[Callable[[int], None]] = None
        self._data_packet_index = 0

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._write_lock: Optional[asyncio.Lock] = None
        # Queues for unsolicited responses (device sends after ACK)
        self._response_queues: dict[int, asyncio.Queue] = {}

    # -- Scanning ----------------------------------------------------------

    @staticmethod
    async def scan(timeout: float = 10.0, serial_filter: str = "") -> list[BLEDevice]:
        """Scan for WatchPAT devices. They advertise as 'ITAMAR_XXXXXXX'."""
        found = {}  # address -> device

        def detection_callback(device, adv_data):
            name = adv_data.local_name or device.name or ""
            if "ITAMAR_" in name and device.address not in found:
                suffix = name.replace("ITAMAR_", "").replace("N", "")
                try:
                    serial = f"{int(suffix, 16):09d}"
                except ValueError:
                    serial = suffix
                if serial_filter and serial != serial_filter:
                    return
                logger.info("Found WatchPAT: %s (serial: %s, addr: %s)",
                            name, serial, device.address)
                found[device.address] = device

        scanner = BleakScanner(detection_callback=detection_callback)
        await scanner.start()
        await asyncio.sleep(timeout)
        await scanner.stop()
        return list(found.values())

    # -- Connection --------------------------------------------------------

    async def connect(self, device: BLEDevice) -> bool:
        """Connect to a WatchPAT device and enable TX notifications."""
        self._loop = asyncio.get_running_loop()
        self._write_lock = asyncio.Lock()
        self.device = device
        self.client = BleakClient(device, timeout=15.0)
        await self.client.connect()
        self.connected = True
        logger.info("Connected to %s", device.address)

        # Enable notifications on TX characteristic
        await self.client.start_notify(NUS_TX_CHAR_UUID, self._on_notify)
        logger.info("TX notifications enabled")
        return True

    async def disconnect(self):
        """Disconnect from the device."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
        self.connected = False
        logger.info("Disconnected")

    # -- Sending -----------------------------------------------------------

    async def _send_chunks(self, chunks: list[bytes]):
        """Send chunked packet data over BLE RX characteristic (must hold lock)."""
        for i, chunk in enumerate(chunks):
            logger.debug("TX chunk %d/%d: %s", i + 1, len(chunks), chunk.hex())
            await self.client.write_gatt_char(NUS_RX_CHAR_UUID, chunk,
                                               response=False)
            await asyncio.sleep(0.01)  # Small delay between chunks

    async def _send_command(self, chunks: list[bytes], packet_id: int,
                            timeout: float = 5.0) -> Optional[tuple]:
        """Send a command and wait for a response matched by packet_id."""
        fut = self._loop.create_future()
        self.pending_responses[packet_id] = fut
        async with self._write_lock:
            await self._send_chunks(chunks)
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for response to packet %d", packet_id)
            self.pending_responses.pop(packet_id, None)
            return None

    async def _send_ack(self, response_opcode: int, status: int,
                        response_id: int):
        """Send an ACK for a received response (acquires write lock)."""
        chunks = build_ack(response_opcode, status, response_id)
        async with self._write_lock:
            await self._send_chunks(chunks)

    def _register_response_queue(self, opcode: int) -> asyncio.Queue:
        """Register a queue to receive a specific response opcode."""
        q = asyncio.Queue(maxsize=1)
        self._response_queues[opcode] = q
        return q

    async def _wait_for_response(self, opcode: int, timeout: float = 10.0):
        """Wait for a specific unsolicited response type."""
        q = self._register_response_queue(opcode)
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for response opcode 0x%04x", opcode)
            return None
        finally:
            self._response_queues.pop(opcode, None)

    # -- Notification handler ----------------------------------------------

    def _on_notify(self, _sender, data: bytearray):
        """Handle incoming BLE notifications (called by bleak on bg thread)."""
        packet = self.reassembler.feed(bytes(data))
        if packet is None:
            return  # Still reassembling

        hdr = parse_header(packet)
        if hdr is None:
            logger.warning("Failed to parse header")
            return

        opcode = hdr["opcode"]
        packet_id = hdr["packet_id"]
        payload = packet[HEADER_SIZE:]
        opcode_dep = hdr.get("opcode_dependent", 0)

        logger.debug("Received opcode=0x%04x id=%d len=%d",
                     opcode, packet_id, len(packet))

        # Resolve pending future for ACKs
        if opcode == ResponseOpcode.ACK:
            ack_status = payload[2] if len(payload) > 2 else 0
            orig_opcode = struct.unpack_from(">H", payload, 0)[0] if len(payload) >= 2 else 0
            logger.info("ACK received: opcode=0x%04x status=%d id=%d",
                        orig_opcode, ack_status, packet_id)
            fut = self.pending_responses.pop(packet_id, None)
            if fut and not fut.done():
                self._loop.call_soon_threadsafe(
                    fut.set_result, ("ack", ack_status, orig_opcode))
            return

        # Handle different response types
        self._handle_response(opcode, packet_id, payload, opcode_dep, packet)

    def _resolve_future(self, packet_id: int, value):
        """Thread-safe resolve of a pending response future."""
        fut = self.pending_responses.pop(packet_id, None)
        if fut and not fut.done():
            self._loop.call_soon_threadsafe(fut.set_result, value)

    def _schedule_ack(self, opcode: int, status: int, packet_id: int):
        """Schedule an ACK to be sent from the event loop."""
        self._loop.call_soon_threadsafe(
            asyncio.ensure_future,
            self._send_ack(opcode, status, packet_id)
        )

    def _enqueue_response(self, opcode: int, value):
        """Push a parsed response to a waiting queue, if registered."""
        q = self._response_queues.get(opcode)
        if q:
            self._loop.call_soon_threadsafe(q.put_nowait, value)
            return True
        return False

    def _handle_response(self, opcode: int, packet_id: int, payload: bytes,
                         opcode_dep: int, raw_packet: bytes):
        """Process a non-ACK response from the device."""

        if opcode in (ResponseOpcode.SESSION_CONFIRM, ResponseOpcode.CONFIG_RESPONSE):
            cfg = parse_device_config(payload)
            self.config = cfg
            logger.info("Device config: %s", cfg)
            if self.on_config:
                self.on_config(cfg)
            self._schedule_ack(opcode, 0, packet_id)
            self._resolve_future(packet_id, ("config", cfg))
            self._enqueue_response(opcode, cfg)

        elif opcode == ResponseOpcode.TECH_STATUS:
            ts = parse_tech_status(payload)
            logger.info("Tech status: %s", ts)
            if self.on_tech_status:
                self.on_tech_status(ts)
            self._schedule_ack(opcode, 0, packet_id)
            self._enqueue_response(opcode, ts)

        elif opcode == ResponseOpcode.DATA_PACKET:
            logger.debug("Data packet received (id=%d, %d bytes payload)",
                         packet_id, len(payload))
            if self.on_data_packet:
                self.on_data_packet(payload)
            if self.on_parsed_data:
                try:
                    parsed = parse_data_packet(payload, self._data_packet_index)
                    self._data_packet_index += 1
                    self.on_parsed_data(parsed)
                except Exception as e:
                    logger.error("Failed to parse data packet: %s", e)
            self._schedule_ack(opcode, 0, packet_id)

        elif opcode == ResponseOpcode.BIT_RESPONSE:
            bit = parse_bit_response(payload)
            logger.info("BIT result: %s", bit)
            if self.on_bit_result:
                self.on_bit_result(bit)
            self._schedule_ack(opcode, 0, packet_id)
            self._enqueue_response(opcode, bit)

        elif opcode == ResponseOpcode.IS_PAIRED_RESPONSE:
            paired_status = payload[2] if len(payload) > 2 else 0
            logger.info("Device paired status: %d (opcode_dep: %d)",
                        paired_status, opcode_dep)
            self._schedule_ack(opcode, 0, packet_id)
            self._resolve_future(packet_id, ("paired", paired_status, opcode_dep))
            self._enqueue_response(opcode, ("paired", paired_status, opcode_dep))

        elif opcode == ResponseOpcode.ERROR_STATUS:
            error_code = payload[0] if payload else 0
            logger.warning("Device error: %d", error_code)
            if self.on_error:
                self.on_error(error_code)
            self._schedule_ack(opcode, 0, packet_id)

        elif opcode == ResponseOpcode.END_OF_TEST:
            logger.info("End of test data (reason: %d)", opcode_dep)
            if self.on_end_of_test:
                self.on_end_of_test(opcode_dep)
            self._schedule_ack(opcode, 0, packet_id)

        elif opcode == ResponseOpcode.FINGER_TEST:
            if len(payload) >= 4:
                result = struct.unpack_from("<I", payload, 0)[0]
            else:
                result = 0
            logger.info("Finger test result: %d", result)
            if self.on_finger_test:
                self.on_finger_test(result)
            self._schedule_ack(opcode, 0, packet_id)
            self._enqueue_response(opcode, result)

        elif opcode == ResponseOpcode.RESET_REASON_RESPONSE:
            logger.info("Reset reason: %d", opcode_dep)
            self._schedule_ack(opcode, 0, packet_id)

        else:
            logger.info("Unhandled opcode: 0x%04x (%d bytes payload)",
                        opcode, len(payload))
            self._schedule_ack(opcode, 0, packet_id)

    # -- High-level commands -----------------------------------------------

    async def is_device_paired(self) -> Optional[tuple]:
        """Check if the device is paired.
        Device may ACK first and then send IS_PAIRED_RESPONSE, or respond directly.
        """
        chunks, pid = build_is_device_paired()
        result = await self._send_command(chunks, pid)
        if result and result[0] == "paired":
            return result
        # Got an ACK, wait for the actual response
        if result and result[0] == "ack":
            q = self._register_response_queue(ResponseOpcode.IS_PAIRED_RESPONSE)
            try:
                # IS_PAIRED_RESPONSE handler resolves futures by packet_id,
                # but the device uses its own ID. Use queue instead.
                val = await asyncio.wait_for(q.get(), timeout=5.0)
                return val
            except asyncio.TimeoutError:
                return result  # Return the ACK info
            finally:
                self._response_queues.pop(ResponseOpcode.IS_PAIRED_RESPONSE, None)
        return result

    async def request_tech_status(self) -> Optional[TechStatus]:
        """Request technical status (battery, LEDs, etc.).
        Device ACKs the request, then sends TECH_STATUS as a separate packet.
        """
        q = self._register_response_queue(ResponseOpcode.TECH_STATUS)
        chunks, pid = build_tech_status_request()
        ack = await self._send_command(chunks, pid, timeout=5.0)
        if not ack:
            self._response_queues.pop(ResponseOpcode.TECH_STATUS, None)
            return None
        try:
            return await asyncio.wait_for(q.get(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for TECH_STATUS data")
            return None
        finally:
            self._response_queues.pop(ResponseOpcode.TECH_STATUS, None)

    async def request_bit(self, flags: int = 0) -> Optional[BITResult]:
        """Request Built-In Test.
        Device ACKs, then sends BIT_RESPONSE separately.
        """
        q = self._register_response_queue(ResponseOpcode.BIT_RESPONSE)
        chunks, pid = build_bit_request(flags)
        ack = await self._send_command(chunks, pid, timeout=5.0)
        if not ack:
            self._response_queues.pop(ResponseOpcode.BIT_RESPONSE, None)
            return None
        try:
            return await asyncio.wait_for(q.get(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for BIT_RESPONSE data")
            return None
        finally:
            self._response_queues.pop(ResponseOpcode.BIT_RESPONSE, None)

    async def start_finger_detection(self) -> Optional[int]:
        """Start finger detection test."""
        q = self._register_response_queue(ResponseOpcode.FINGER_TEST)
        chunks, pid = build_start_finger_detection()
        ack = await self._send_command(chunks, pid, timeout=5.0)
        if not ack:
            self._response_queues.pop(ResponseOpcode.FINGER_TEST, None)
            return None
        try:
            return await asyncio.wait_for(q.get(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for FINGER_TEST data")
            return None
        finally:
            self._response_queues.pop(ResponseOpcode.FINGER_TEST, None)

    async def start_session(self, mode: int = 1) -> Optional[DeviceConfig]:
        """Start a session. Returns device config on success.
        Device responds with SESSION_CONFIRM containing config.
        """
        chunks, pid = build_session_start(mode=mode)
        result = await self._send_command(chunks, pid, timeout=10.0)
        if result and result[0] == "config":
            return result[1]
        # Also check if we got an ACK (device may send config separately)
        if result and result[0] == "ack":
            q = self._register_response_queue(ResponseOpcode.SESSION_CONFIRM)
            try:
                return await asyncio.wait_for(q.get(), timeout=10.0)
            except asyncio.TimeoutError:
                return None
            finally:
                self._response_queues.pop(ResponseOpcode.SESSION_CONFIRM, None)
        return None

    async def start_acquisition(self):
        """Start data acquisition."""
        chunks, pid = build_start_acquisition()
        return await self._send_command(chunks, pid)

    async def stop_acquisition(self):
        """Stop data acquisition."""
        chunks, pid = build_stop_acquisition()
        return await self._send_command(chunks, pid)

    async def set_leds(self, mask: int):
        """Set LED state."""
        chunks, pid = build_set_leds(mask)
        return await self._send_command(chunks, pid)


# ---------------------------------------------------------------------------
# Offline replay — read a length-prefixed .dat file
# ---------------------------------------------------------------------------

def read_dat_file(path: str):
    """Yield raw DATA_PACKET payloads from a length-prefixed .dat file."""
    with open(path, "rb") as f:
        while True:
            hdr = f.read(4)
            if len(hdr) < 4:
                break
            length = struct.unpack("<I", hdr)[0]
            payload = f.read(length)
            if len(payload) < length:
                break
            yield payload


def format_parsed_packet(pkt: ParsedDataPacket) -> str:
    """Format a ParsedDataPacket for human-readable display."""
    parts = []
    for wf in pkt.waveforms:
        n = len(wf.samples)
        if n > 0:
            mn, mx = min(wf.samples), max(wf.samples)
            last = wf.samples[-1]
            parts.append(f"{wf.channel_name}({n}s [{mn}..{mx}] @{last})")
        else:
            parts.append(f"{wf.channel_name}(empty)")
    if pkt.metric is not None:
        parts.append(f"Metric={pkt.metric.value}")
    if pkt.motion is not None:
        m = pkt.motion
        n_ok = sum(1 for sf in m.subframes if sf.crc_valid)
        pos = m.body_position
        if m.subframes:
            sf = m.subframes[-1]
            parts.append(
                f"Motion({n_ok}/{len(m.subframes)}crc "
                f"xyz=({sf.x},{sf.y},{sf.z}) pos={pos} "
                f"a={sf.field_a} b={sf.field_b})"
            )
    for ev in pkt.events:
        parts.append(f"Event({ev.kind.name} val={ev.value})")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
async def main():
    parser = argparse.ArgumentParser(
        description="WatchPAT ONE BLE Client — reverse-engineered protocol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --scan-only                     Scan for nearby devices
  %(prog)s --serial XXXXXXXXX --bit        Connect and run Built-In Test
  %(prog)s --serial XXXXXXXXX --monitor    Start recording session & decode
  %(prog)s --serial XXXXXXXXX --finger     Run finger detection test
  %(prog)s --replay capture.dat            Replay and decode an offline dump
  %(prog)s --replay capture.dat --csv out  Export decoded channels to CSV
""")
    parser.add_argument("--serial", type=str, default="",
                        help="Device serial number to connect to")
    parser.add_argument("--scan-only", action="store_true",
                        help="Only scan for devices, don't connect")
    parser.add_argument("--scan-time", type=float, default=10.0,
                        help="Scan duration in seconds (default: 10)")
    parser.add_argument("--tech-status", action="store_true",
                        help="Request technical status after connecting")
    parser.add_argument("--bit", action="store_true",
                        help="Run Built-In Test after connecting")
    parser.add_argument("--finger", action="store_true",
                        help="Run finger detection test")
    parser.add_argument("--monitor", action="store_true",
                        help="Start recording session and capture data")
    parser.add_argument("--output", "-o", type=str, default="",
                        help="Output file for raw data (default: watchpat_<serial>_<timestamp>.dat)")
    parser.add_argument("--duration", type=int, default=0,
                        help="Recording duration in seconds (0=until Ctrl+C)")
    parser.add_argument("--replay", type=str, default="",
                        help="Replay a .dat capture file offline (decode & display)")
    parser.add_argument("--csv", type=str, default="",
                        help="Export decoded data to CSV prefix (e.g. 'out' -> out_OxiA.csv, ...)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    # ------------------------------------------------------------------
    # Offline replay mode
    # ------------------------------------------------------------------
    if args.replay:
        print(f"Replaying {args.replay} ...")
        csv_files = {}
        csv_writers = {}

        if args.csv:
            import csv as csv_mod
            # We'll open CSV files lazily per channel

        pkt_count = 0
        total_samples = {}
        for raw in read_dat_file(args.replay):
            pkt = parse_data_packet(raw, pkt_count)
            pkt_count += 1

            # Display
            summary = format_parsed_packet(pkt)
            if pkt_count <= 5 or pkt_count % 100 == 0:
                print(f"  [{pkt_count:6d}] {summary}")

            # CSV export
            if args.csv:
                import csv as csv_mod
                for wf in pkt.waveforms:
                    ch = wf.channel_name
                    total_samples[ch] = total_samples.get(ch, 0) + len(wf.samples)
                    if ch not in csv_files:
                        path = f"{args.csv}_{ch}.csv"
                        f = open(path, "w", newline="")
                        w = csv_mod.writer(f)
                        w.writerow(["packet", "sample_idx", "value"])
                        csv_files[ch] = f
                        csv_writers[ch] = w
                    w = csv_writers[ch]
                    for i, v in enumerate(wf.samples):
                        w.writerow([pkt_count - 1, i, v])

                if pkt.metric is not None:
                    ch = "Metric"
                    total_samples[ch] = total_samples.get(ch, 0) + 1
                    if ch not in csv_files:
                        path = f"{args.csv}_{ch}.csv"
                        f = open(path, "w", newline="")
                        w = csv_mod.writer(f)
                        w.writerow(["packet", "value"])
                        csv_files[ch] = f
                        csv_writers[ch] = w
                    csv_writers[ch].writerow([pkt_count - 1, pkt.metric.value])

                if pkt.motion is not None:
                    ch = "Motion"
                    total_samples[ch] = total_samples.get(ch, 0) + len(pkt.motion.subframes)
                    if ch not in csv_files:
                        path = f"{args.csv}_{ch}.csv"
                        f = open(path, "w", newline="")
                        w = csv_mod.writer(f)
                        w.writerow(["packet", "subframe", "field_a", "field_b",
                                    "x", "y", "z", "crc_valid", "body_pos"])
                        csv_files[ch] = f
                        csv_writers[ch] = w
                    w = csv_writers[ch]
                    for i, sf in enumerate(pkt.motion.subframes):
                        w.writerow([pkt_count - 1, i, sf.field_a, sf.field_b,
                                    sf.x, sf.y, sf.z, sf.crc_valid,
                                    pkt.motion.body_position])

        # Close CSV files
        for f in csv_files.values():
            f.close()

        print(f"\nReplay complete: {pkt_count} packets")
        for ch, n in sorted(total_samples.items()):
            print(f"  {ch}: {n} samples")
        if args.csv:
            for ch in sorted(csv_files):
                print(f"  Exported: {args.csv}_{ch}.csv")
        return

    # ------------------------------------------------------------------
    # Live BLE mode
    # ------------------------------------------------------------------
    wp = WatchPATClient()

    # Scan
    print(f"Scanning for WatchPAT devices ({args.scan_time}s)...")
    devices = await wp.scan(timeout=args.scan_time, serial_filter=args.serial)

    if not devices:
        print("No WatchPAT devices found.")
        return

    for d in devices:
        name = d.name or "Unknown"
        print(f"  Found: {name} ({d.address})")

    if args.scan_only:
        return

    # Connect to first device
    device = devices[0]
    print(f"\nConnecting to {device.name} ({device.address})...")
    await wp.connect(device)

    try:
        # Check pairing
        print("\nChecking device pairing status...")
        result = await wp.is_device_paired()
        if result:
            print(f"  Pairing response: {result}")

        # Start session to get config
        print("\nStarting session...")
        config = await wp.start_session()
        if config:
            print(f"  Device config: {config}")
        else:
            print("  (No config response - device may need pairing first)")

        # Tech status (before acquisition)
        if args.tech_status:
            print("\nRequesting technical status...")
            ts = await wp.request_tech_status()
            if ts:
                print(f"  {ts}")
            else:
                print("  (No response - tech status may require active acquisition)")

        # BIT
        if args.bit:
            print("\nRunning Built-In Test...")
            bit = await wp.request_bit()
            if bit:
                print(f"  {bit}")

        # Finger detection
        if args.finger:
            print("\nRunning finger detection...")
            finger = await wp.start_finger_detection()
            if finger is not None:
                on_finger = "DETECTED" if finger == 1 else "NOT DETECTED"
                print(f"  Finger: {on_finger} (raw={finger})")

        # Monitor / recording mode
        if args.monitor:
            # Set up recording output
            ts_str = time.strftime("%Y%m%d_%H%M%S")
            sn = config.serial_number if config else "unknown"
            out_path = args.output or f"watchpat_{sn}_{ts_str}.dat"

            data_count = 0
            total_bytes = 0
            start_time = None
            last_motion_pos = ""
            last_metric = 0
            channel_samples = {}
            dump_file = open(out_path, "wb")

            def on_data(payload):
                nonlocal data_count, total_bytes, start_time
                data_count += 1
                total_bytes += len(payload)
                if start_time is None:
                    start_time = time.time()
                # Write length-prefixed packets for easy re-reading
                dump_file.write(struct.pack("<I", len(payload)))
                dump_file.write(payload)
                dump_file.flush()

            def on_parsed(pkt: ParsedDataPacket):
                nonlocal last_motion_pos, last_metric
                for wf in pkt.waveforms:
                    ch = wf.channel_name
                    channel_samples[ch] = channel_samples.get(ch, 0) + len(wf.samples)
                if pkt.motion is not None:
                    last_motion_pos = pkt.motion.body_position
                if pkt.metric is not None:
                    last_metric = pkt.metric.value

            def on_error(code):
                logger.warning("Device error: %d", code)

            def on_end(reason):
                logger.info("End of test (reason: %d)", reason)

            wp.on_data_packet = on_data
            wp.on_parsed_data = on_parsed
            wp.on_error = on_error
            wp.on_end_of_test = on_end

            # Start acquisition
            print("\nStarting data acquisition...")
            acq_result = await wp.start_acquisition()
            if acq_result:
                ack_status = acq_result[1] if len(acq_result) > 1 else "?"
                status_str = "OK" if ack_status == 0 else f"status={ack_status}"
                print(f"  Acquisition started ({status_str})")
            else:
                print("  WARNING: No ACK for StartAcquisition")

            print(f"\nRecording to {out_path}")
            print("Waiting for data packets (~40s warmup)...")
            if args.duration > 0:
                print(f"Will record for {args.duration}s then stop.")
            else:
                print("Press Ctrl+C to stop recording.")

            try:
                last_report = 0
                while True:
                    await asyncio.sleep(1)
                    elapsed = time.time() - start_time if start_time else 0
                    # Periodic status line with decoded info
                    if data_count > 0 and data_count != last_report:
                        rate = data_count / elapsed if elapsed > 0 else 0
                        ch_str = " ".join(
                            f"{k}:{v}" for k, v in sorted(channel_samples.items()))
                        pos_str = f" pos={last_motion_pos}" if last_motion_pos else ""
                        print(f"\r  Pkts:{data_count} "
                              f"{total_bytes/1024:.1f}KB "
                              f"{rate:.1f}p/s "
                              f"{int(elapsed)}s "
                              f"[{ch_str}]{pos_str} "
                              f"met={last_metric}   ",
                              end="", flush=True)
                        last_report = data_count
                    # Check duration limit
                    if args.duration > 0 and elapsed >= args.duration:
                        print()
                        break
            except KeyboardInterrupt:
                print()

            print("Stopping acquisition...")
            await wp.stop_acquisition()
            dump_file.close()
            elapsed = time.time() - start_time if start_time else 0
            print(f"\nRecording complete:")
            print(f"  Packets: {data_count}")
            print(f"  Data: {total_bytes/1024:.1f} KB")
            print(f"  Duration: {int(elapsed)}s")
            for ch, n in sorted(channel_samples.items()):
                print(f"  {ch}: {n} total samples")
            print(f"  Saved to: {out_path}")

    finally:
        await wp.disconnect()
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
