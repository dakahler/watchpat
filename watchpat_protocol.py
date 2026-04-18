"""
WatchPAT ONE shared protocol primitives.

Single source of truth for binary parsing across watchpat_ble.py and
watchpat_to_resmed_sd.py.  Structural parsing is provided by the
Kaitai-generated WatchpatPacket class (kaitai/python/watchpat_packet.py);
this module adds the parts that Kaitai cannot express declaratively:

  - CRC-16 CCITT computation and verification
  - Stateful waveform decoders (zigzag byte-delta, nibble-delta)
  - CRC validation for motion subframes (requires reconstructing raw bytes)
  - Convenience wrappers: parse_packet(), parse_data_payload()

DO NOT edit the generated file kaitai/python/watchpat_packet.py directly.
Edit watchpat.ksy and run generate.sh to regenerate it.
"""

import os
import struct
import sys

# Make the generated Kaitai output importable regardless of working directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "kaitai", "python"))

from watchpat_packet import WatchpatPacket  # noqa: E402 (path set above)
from kaitaistruct import KaitaiStream, BytesIO, ValidationNotEqualError  # noqa: E402

__all__ = [
    # Kaitai types
    "WatchpatPacket",
    "KaitaiStream",
    "BytesIO",
    "ValidationNotEqualError",
    # Convenience parsers
    "parse_packet",
    "parse_data_payload",
    # CRC
    "crc16_watchpat",
    "verify_crc",
    # Waveform decoders
    "decode_byte_delta_waveform",
    "decode_nibble_delta_waveform",
    # Motion subframe CRC
    "motion_subframe_crc_valid",
    # Record kind constants  (record_id << 8 | record_type)
    "RECORD_KIND_OXIA",
    "RECORD_KIND_OXIB",
    "RECORD_KIND_PAT",
    "RECORD_KIND_CHEST",
    "RECORD_KIND_METRIC",
    "RECORD_KIND_MOTION",
    "RECORD_KIND_EVENT_0C",
    "RECORD_KIND_EVENT_0D",
]

# ---------------------------------------------------------------------------
# Record kind constants
# ---------------------------------------------------------------------------
RECORD_KIND_OXIA     = 0x0111   # Oximetry channel A — byte-delta waveform
RECORD_KIND_OXIB     = 0x0211   # Oximetry channel B — byte-delta waveform
RECORD_KIND_PAT      = 0x0311   # PAT waveform        — byte-delta waveform
RECORD_KIND_CHEST    = 0x0401   # Chest / respiratory — nibble-delta waveform
RECORD_KIND_METRIC   = 0x0510   # Once-per-second metric (i32 LE)
RECORD_KIND_MOTION   = 0x0600   # Motion / accel summary (5 subframes)
RECORD_KIND_EVENT_0C = 0x0C00   # Event code (u16 LE)
RECORD_KIND_EVENT_0D = 0x0D00   # Event payload (20 bytes)


# ---------------------------------------------------------------------------
# Convenience parsers
# ---------------------------------------------------------------------------

def parse_packet(data: bytes) -> WatchpatPacket:
    """Parse a complete WatchPAT BLE packet (24-byte header + body)."""
    return WatchpatPacket(KaitaiStream(BytesIO(data)))


def parse_data_payload(payload: bytes) -> "WatchpatPacket.DataPacketPayload":
    """Parse a DATA_PACKET payload (raw bytes *after* the 24-byte header)."""
    return WatchpatPacket.DataPacketPayload(
        KaitaiStream(BytesIO(payload)), None, None
    )


# ---------------------------------------------------------------------------
# CRC-16 CCITT (polynomial 0x1021, init 0xFFFF, bit-by-bit)
# Matches the original Java implementation in WatchPatProtocol.java.
# ---------------------------------------------------------------------------

def crc16_watchpat(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        mask = 0x80
        while mask > 0:
            carry = (crc & 0x8000) != 0
            if (byte & mask) != 0:
                carry = not carry
            crc = (crc << 1) & 0xFFFF
            if carry:
                crc ^= 0x1021
            mask >>= 1
    return crc & 0xFFFF


def verify_crc(data: bytes) -> bool:
    """Verify the CRC of a received packet (stored at bytes 22-23, LE)."""
    if len(data) < 24:
        return False
    packet = bytearray(data)
    stored = packet[22] | (packet[23] << 8)
    packet[22] = 0
    packet[23] = 0
    computed = crc16_watchpat(bytes(packet))
    # The device stores CRC as Short.reverseBytes(computed); accept both forms.
    reversed_computed = ((computed >> 8) & 0xFF) | ((computed & 0xFF) << 8)
    return stored == computed or stored == reversed_computed


# ---------------------------------------------------------------------------
# Stateful waveform decoders
# Returns a list of signed integer samples.
# ---------------------------------------------------------------------------

def decode_byte_delta_waveform(payload: bytes) -> list:
    """Decode a zigzag byte-delta waveform payload (records 01/11, 02/11, 03/11).

    Layout: 2-byte LE signed seed, then zigzag8-encoded first-order deltas.
    Zigzag decode: delta = (b >> 1) ^ -(b & 1)
    """
    if len(payload) < 2:
        return []
    seed = struct.unpack_from("<h", payload, 0)[0]
    samples = [seed]
    acc = seed
    for b in payload[2:]:
        acc += (b >> 1) ^ -(b & 1)
        samples.append(acc)
    return samples


def decode_nibble_delta_waveform(payload: bytes) -> list:
    """Decode a nibble-delta waveform payload (record 04/01, chest sensor).

    Layout: 2-byte LE signed seed, 1 skipped byte, then nibble-encoded deltas.
    Low nibble = signed 4-bit delta; high nibble 0 or 7 = one extra repeated sample.
    """
    if len(payload) < 3:
        return []
    seed = struct.unpack_from("<h", payload, 0)[0]
    samples = [seed]
    acc = seed
    for b in payload[3:]:
        lo = b & 0x0F
        hi = (b >> 4) & 0x0F
        delta = lo - 16 if lo >= 8 else lo
        acc += delta
        samples.append(acc)
        if hi == 0 or hi == 7:
            samples.append(acc)
    return samples


# ---------------------------------------------------------------------------
# Motion subframe CRC
# ---------------------------------------------------------------------------

def motion_subframe_crc_valid(sf: "WatchpatPacket.MotionSubframe") -> bool:
    """Return True if the CRC-16 in a parsed MotionSubframe is correct.

    Reconstructs the 14 header bytes from the individual parsed fields, then
    recomputes CRC-16 and compares against the stored value.
    """
    data = struct.pack(
        "<IHHhhh",
        0x57A3DDDD,
        sf.field_a,
        sf.field_b,
        sf.x,
        sf.y,
        sf.z,
    )
    return crc16_watchpat(data) == sf.crc
