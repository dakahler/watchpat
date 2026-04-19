"""Unit tests for watchpat_protocol.py against tests/testdata.dat.

testdata.dat format: repeated [4-byte LE length][full packet bytes].
All 15 packets are DATA_PACKET (opcode 0x0800), Android format (24-byte
header + payload).  Each payload begins with a 3-byte sub-header
[record_count, 0x01, 0x00] followed by contiguous logical records.
"""

import os
import struct
import sys
import unittest

# Locate the project root so imports work regardless of cwd.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from watchpat_protocol import (
    RECORD_KIND_CHEST,
    RECORD_KIND_EVENT_0D,
    RECORD_KIND_METRIC,
    RECORD_KIND_MOTION,
    RECORD_KIND_OXIA,
    RECORD_KIND_OXIB,
    RECORD_KIND_PAT,
    crc16_watchpat,
    decode_byte_delta_waveform,
    decode_nibble_delta_waveform,
    motion_subframe_crc_valid,
    parse_packet,
    parse_data_payload,
    verify_crc,
)

TESTDATA = os.path.join(os.path.dirname(__file__), "testdata.dat")


def _load_packets():
    """Return list of raw packet bytes from testdata.dat."""
    packets = []
    with open(TESTDATA, "rb") as f:
        while True:
            hdr = f.read(4)
            if len(hdr) < 4:
                break
            length = struct.unpack_from("<I", hdr)[0]
            packets.append(f.read(length))
    return packets


PACKETS = _load_packets()


class TestCrc(unittest.TestCase):
    def test_crc_known_value(self):
        # CRC-16 CCITT of empty string with init 0xFFFF = 0xFFFF
        self.assertEqual(crc16_watchpat(b""), 0xFFFF)

    def test_crc_single_byte(self):
        # Sanity: CRC of 0x00 from init 0xFFFF, poly 0x1021
        self.assertEqual(crc16_watchpat(b"\x00"), 0xE1F0)

    def test_verify_crc_packet0(self):
        self.assertTrue(verify_crc(PACKETS[0]))

    def test_verify_crc_all_packets(self):
        for i, pkt in enumerate(PACKETS):
            self.assertTrue(verify_crc(pkt), f"CRC failed on packet {i}")

    def test_crc_detects_corruption(self):
        bad = bytearray(PACKETS[0])
        bad[4] ^= 0xFF   # flip a byte in the timestamp
        self.assertFalse(verify_crc(bytes(bad)))

    def test_verify_crc_rejects_short_packet(self):
        self.assertFalse(verify_crc(b"\xBB\xBB"))

    def test_crc_detects_crc_field_corruption(self):
        bad = bytearray(PACKETS[0])
        bad[22] ^= 0xFF
        self.assertFalse(verify_crc(bytes(bad)))


class TestParsePacket(unittest.TestCase):
    def test_packet0_header_fields(self):
        pkt = parse_packet(PACKETS[0])
        self.assertEqual(pkt.header.signature, b"\xBB\xBB")
        self.assertEqual(pkt.header.opcode, 0x0800)
        self.assertEqual(pkt.header.total_len, len(PACKETS[0]))
        self.assertEqual(pkt.header.packet_id, 2)
        self.assertEqual(pkt.header.timestamp, 5600)
        self.assertEqual(pkt.header.opcode_dependent, 0)
        self.assertEqual(pkt.header.reserved, 0)

    def test_all_packets_full_parse_and_length_match(self):
        for i, raw in enumerate(PACKETS):
            pkt = parse_packet(raw)
            self.assertEqual(pkt.header.total_len, len(raw), f"packet {i} length")
            self.assertEqual(pkt.header.opcode, 0x0800, f"packet {i} opcode")
            self.assertEqual(pkt.body.record_count, len(pkt.body.records),
                             f"packet {i} record count")


class TestParseDataPayload(unittest.TestCase):
    def _payload(self, pkt_index):
        return PACKETS[pkt_index][24:]   # strip 24-byte header

    def test_sub_header_packet0(self):
        dp = parse_data_payload(self._payload(0))
        self.assertEqual(dp.record_count, 7)
        self.assertEqual(dp.sub_header, 0x0001)   # bytes [0x01, 0x00] as LE u16

    def test_sub_header_packet1(self):
        dp = parse_data_payload(self._payload(1))
        self.assertEqual(dp.record_count, 6)

    def test_record_count_packet0(self):
        dp = parse_data_payload(self._payload(0))
        self.assertEqual(len(dp.records), 7)

    def test_record_kinds_packet0(self):
        dp = parse_data_payload(self._payload(0))
        kinds = [(r.record_id << 8) | r.record_type for r in dp.records]
        self.assertIn(RECORD_KIND_OXIA,   kinds)
        self.assertIn(RECORD_KIND_OXIB,   kinds)
        self.assertIn(RECORD_KIND_PAT,    kinds)
        self.assertIn(RECORD_KIND_CHEST,  kinds)
        self.assertIn(RECORD_KIND_METRIC, kinds)
        self.assertIn(RECORD_KIND_MOTION, kinds)
        self.assertIn(RECORD_KIND_EVENT_0D, kinds)

    def test_all_packets_parseable(self):
        for i in range(len(PACKETS)):
            dp = parse_data_payload(self._payload(i))
            self.assertGreater(len(dp.records), 0, f"packet {i} has no records")

    def test_all_packets_record_count_matches_records(self):
        for i in range(len(PACKETS)):
            dp = parse_data_payload(self._payload(i))
            self.assertEqual(dp.record_count, len(dp.records),
                             f"packet {i} record count mismatch")


class TestMetric(unittest.TestCase):
    def _metric_record(self, pkt_index=0):
        dp = parse_data_payload(PACKETS[pkt_index][24:])
        for r in dp.records:
            if (r.record_id << 8) | r.record_type == RECORD_KIND_METRIC:
                return r
        return None

    def test_metric_value_packet0(self):
        rec = self._metric_record(0)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.payload.value, -351)


class TestByteDeltaWaveform(unittest.TestCase):
    def _raw_payload(self, kind, pkt_index=0):
        dp = parse_data_payload(PACKETS[pkt_index][24:])
        for r in dp.records:
            if (r.record_id << 8) | r.record_type == kind:
                return r._raw_payload
        return None

    def test_oxia_seed(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_OXIA))
        self.assertEqual(samples[0], 4434)

    def test_oxia_sample_count(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_OXIA))
        self.assertEqual(len(samples), 101)

    def test_oxia_first_three(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_OXIA))
        self.assertEqual(samples[:3], [4434, 4306, 4178])

    def test_oxia_last_sample(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_OXIA))
        self.assertEqual(samples[-1], 3516)

    def test_oxib_seed(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_OXIB))
        self.assertEqual(samples[0], 14196)

    def test_oxib_first_three(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_OXIB))
        self.assertEqual(samples[:3], [14196, 14196, 14260])

    def test_oxib_last_sample(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_OXIB))
        self.assertEqual(samples[-1], 13286)

    def test_pat_seed(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_PAT))
        self.assertEqual(samples[0], 22269)

    def test_pat_first_three(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_PAT))
        self.assertEqual(samples[:3], [22269, 22269, 22333])

    def test_pat_last_sample(self):
        samples = decode_byte_delta_waveform(self._raw_payload(RECORD_KIND_PAT))
        self.assertEqual(samples[-1], 22027)

    def test_empty_payload_returns_empty(self):
        self.assertEqual(decode_byte_delta_waveform(b""), [])

    def test_single_byte_payload_returns_empty(self):
        self.assertEqual(decode_byte_delta_waveform(b"\x00"), [])

    def test_seed_only_payload_returns_single_sample(self):
        self.assertEqual(decode_byte_delta_waveform(b"\xE8\x03"), [1000])

    def test_handcrafted_negative_and_positive_deltas(self):
        payload = b"\xE8\x03\x01\x04\x03"
        self.assertEqual(
            decode_byte_delta_waveform(payload),
            [1000, 999, 1001, 999],
        )


class TestNibbleDeltaWaveform(unittest.TestCase):
    def _chest_raw(self, pkt_index=0):
        dp = parse_data_payload(PACKETS[pkt_index][24:])
        for r in dp.records:
            if (r.record_id << 8) | r.record_type == RECORD_KIND_CHEST:
                return r._raw_payload
        return None

    def test_chest_seed(self):
        samples = decode_nibble_delta_waveform(self._chest_raw())
        self.assertEqual(samples[0], 1603)

    def test_chest_sample_count(self):
        samples = decode_nibble_delta_waveform(self._chest_raw())
        self.assertEqual(len(samples), 89)

    def test_chest_first_three(self):
        samples = decode_nibble_delta_waveform(self._chest_raw())
        self.assertEqual(samples[:3], [1603, 1602, 1609])

    def test_chest_last_sample(self):
        samples = decode_nibble_delta_waveform(self._chest_raw())
        self.assertEqual(samples[-1], 1575)

    def test_empty_payload_returns_empty(self):
        self.assertEqual(decode_nibble_delta_waveform(b""), [])

    def test_short_payload_returns_empty(self):
        self.assertEqual(decode_nibble_delta_waveform(b"\x00\x00"), [])

    def test_seed_and_skip_only_returns_single_sample(self):
        self.assertEqual(decode_nibble_delta_waveform(b"\xE8\x03\x00"), [1000])

    def test_handcrafted_nibble_deltas_and_repeat(self):
        payload = b"\xE8\x03\x00\x70\x0F"
        self.assertEqual(
            decode_nibble_delta_waveform(payload),
            [1000, 1000, 1000, 999, 999],
        )


class TestMotion(unittest.TestCase):
    EXPECTED_SUBFRAMES = [
        (27, 22, -80, 1092, 219),
        (31, 22, -75, 1085, 219),
        (23, 30, -77, 1080, 225),
        (28, 24, -84, 1082, 234),
        (24, 32, -88, 1087, 240),
    ]

    def _motion_record(self, pkt_index=0):
        dp = parse_data_payload(PACKETS[pkt_index][24:])
        for r in dp.records:
            if (r.record_id << 8) | r.record_type == RECORD_KIND_MOTION:
                return r
        return None

    def test_five_subframes(self):
        rec = self._motion_record()
        self.assertEqual(len(rec.payload.subframes), 5)

    def test_all_subframes_crc_valid(self):
        rec = self._motion_record()
        for i, sf in enumerate(rec.payload.subframes):
            self.assertTrue(motion_subframe_crc_valid(sf), f"subframe {i} CRC failed")

    def test_subframe_xyz_values(self):
        rec = self._motion_record()
        for i, (fa, fb, x, y, z) in enumerate(self.EXPECTED_SUBFRAMES):
            sf = rec.payload.subframes[i]
            self.assertEqual(sf.field_a, fa, f"subframe {i} field_a")
            self.assertEqual(sf.field_b, fb, f"subframe {i} field_b")
            self.assertEqual(sf.x, x,       f"subframe {i} x")
            self.assertEqual(sf.y, y,       f"subframe {i} y")
            self.assertEqual(sf.z, z,       f"subframe {i} z")

    def test_motion_crc_detects_modified_subframe(self):
        rec = self._motion_record()
        sf = rec.payload.subframes[0]

        class ModifiedSubframe:
            field_a = sf.field_a
            field_b = sf.field_b
            x = sf.x + 1
            y = sf.y
            z = sf.z
            crc = sf.crc

        self.assertFalse(motion_subframe_crc_valid(ModifiedSubframe()))


class TestEventRecord(unittest.TestCase):
    def test_event_0d_raw_len(self):
        dp = parse_data_payload(PACKETS[0][24:])
        for r in dp.records:
            if (r.record_id << 8) | r.record_type == RECORD_KIND_EVENT_0D:
                self.assertEqual(len(r._raw_payload), 10)
                return
        self.fail("EVENT_0D record not found in packet 0")

    def test_event_0d_first_byte(self):
        dp = parse_data_payload(PACKETS[0][24:])
        for r in dp.records:
            if (r.record_id << 8) | r.record_type == RECORD_KIND_EVENT_0D:
                self.assertEqual(r._raw_payload[0], 0x10)
                return
        self.fail("EVENT_0D record not found in packet 0")


if __name__ == "__main__":
    unittest.main()
