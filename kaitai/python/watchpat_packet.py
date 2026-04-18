# This is a generated file! Please edit source .ksy file and use kaitai-struct-compiler to rebuild
# type: ignore

import kaitaistruct
from kaitaistruct import KaitaiStruct, KaitaiStream, BytesIO


if getattr(kaitaistruct, 'API_VERSION', (0, 9)) < (0, 11):
    raise Exception("Incompatible Kaitai Struct Python API: 0.11 or later is required, but you have %s" % (kaitaistruct.__version__))

class WatchpatPacket(KaitaiStruct):
    def __init__(self, _io, _parent=None, _root=None):
        super(WatchpatPacket, self).__init__(_io)
        self._parent = _parent
        self._root = _root or self
        self._read()

    def _read(self):
        self.header = WatchpatPacket.PacketHeader(self._io, self, self._root)
        _on = self.header.opcode
        if _on == 1280:
            pass
            self._raw_body = self._io.read_bytes(self.header.total_len - 24)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = WatchpatPacket.SessionConfirmPayload(_io__raw_body, self, self._root)
        elif _on == 2048:
            pass
            self._raw_body = self._io.read_bytes(self.header.total_len - 24)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = WatchpatPacket.DataPacketPayload(_io__raw_body, self, self._root)
        elif _on == 4864:
            pass
            self._raw_body = self._io.read_bytes(self.header.total_len - 24)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = WatchpatPacket.BitResponsePayload(_io__raw_body, self, self._root)
        elif _on == 512:
            pass
            self._raw_body = self._io.read_bytes(self.header.total_len - 24)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = WatchpatPacket.SessionConfirmPayload(_io__raw_body, self, self._root)
        elif _on == 5632:
            pass
            self._raw_body = self._io.read_bytes(self.header.total_len - 24)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = WatchpatPacket.TechStatusPayload(_io__raw_body, self, self._root)
        else:
            pass
            self._raw_body = self._io.read_bytes(self.header.total_len - 24)
            _io__raw_body = KaitaiStream(BytesIO(self._raw_body))
            self.body = WatchpatPacket.RawPayload(_io__raw_body, self, self._root)


    def _fetch_instances(self):
        pass
        self.header._fetch_instances()
        _on = self.header.opcode
        if _on == 1280:
            pass
            self.body._fetch_instances()
        elif _on == 2048:
            pass
            self.body._fetch_instances()
        elif _on == 4864:
            pass
            self.body._fetch_instances()
        elif _on == 512:
            pass
            self.body._fetch_instances()
        elif _on == 5632:
            pass
            self.body._fetch_instances()
        else:
            pass
            self.body._fetch_instances()

    class BitResponsePayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.BitResponsePayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.raw_flags = self._io.read_u4le()


        def _fetch_instances(self):
            pass

        @property
        def actigraph_error(self):
            if hasattr(self, '_m_actigraph_error'):
                return self._m_actigraph_error

            self._m_actigraph_error = self.raw_flags & 4 != 0
            return getattr(self, '_m_actigraph_error', None)

        @property
        def battery_depleted(self):
            if hasattr(self, '_m_battery_depleted'):
                return self._m_battery_depleted

            self._m_battery_depleted = self.raw_flags & 1 != 0
            return getattr(self, '_m_battery_depleted', None)

        @property
        def battery_low(self):
            if hasattr(self, '_m_battery_low'):
                return self._m_battery_low

            self._m_battery_low = self.raw_flags & 2 != 0
            return getattr(self, '_m_battery_low', None)

        @property
        def flash_error(self):
            if hasattr(self, '_m_flash_error'):
                return self._m_flash_error

            self._m_flash_error = self.raw_flags & 64 != 0
            return getattr(self, '_m_flash_error', None)

        @property
        def naf_error(self):
            if hasattr(self, '_m_naf_error'):
                return self._m_naf_error

            self._m_naf_error = self.raw_flags & 8 != 0
            return getattr(self, '_m_naf_error', None)

        @property
        def probe_failure(self):
            if hasattr(self, '_m_probe_failure'):
                return self._m_probe_failure

            self._m_probe_failure = self.raw_flags & 512 != 0
            return getattr(self, '_m_probe_failure', None)

        @property
        def probe_led_error(self):
            if hasattr(self, '_m_probe_led_error'):
                return self._m_probe_led_error

            self._m_probe_led_error = self.raw_flags & 128 != 0
            return getattr(self, '_m_probe_led_error', None)

        @property
        def probe_photo_error(self):
            if hasattr(self, '_m_probe_photo_error'):
                return self._m_probe_photo_error

            self._m_probe_photo_error = self.raw_flags & 256 != 0
            return getattr(self, '_m_probe_photo_error', None)

        @property
        def spb_error(self):
            if hasattr(self, '_m_spb_error'):
                return self._m_spb_error

            self._m_spb_error = self.raw_flags & 1024 != 0
            return getattr(self, '_m_spb_error', None)

        @property
        def used_device(self):
            if hasattr(self, '_m_used_device'):
                return self._m_used_device

            self._m_used_device = self.raw_flags & 32 != 0
            return getattr(self, '_m_used_device', None)

        @property
        def vdd_error(self):
            if hasattr(self, '_m_vdd_error'):
                return self._m_vdd_error

            self._m_vdd_error = self.raw_flags & 16 != 0
            return getattr(self, '_m_vdd_error', None)


    class DataPacketPayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.DataPacketPayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.record_count = self._io.read_u1()
            self.sub_header = self._io.read_u2le()
            self.records = []
            i = 0
            while not self._io.is_eof():
                self.records.append(WatchpatPacket.LogicalRecord(self._io, self, self._root))
                i += 1



        def _fetch_instances(self):
            pass
            for i in range(len(self.records)):
                pass
                self.records[i]._fetch_instances()



    class LogicalRecord(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.LogicalRecord, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.sync = self._io.read_bytes(2)
            if not self.sync == b"\xAA\xAA":
                raise kaitaistruct.ValidationNotEqualError(b"\xAA\xAA", self.sync, self._io, u"/types/logical_record/seq/0")
            self.record_id = self._io.read_u1()
            self.record_type = self._io.read_u1()
            self.payload_len = self._io.read_u2le()
            self.rate = self._io.read_u2le()
            self.flags = self._io.read_u4le()
            _on = self.record_id << 8 | self.record_type
            if _on == 1296:
                pass
                self._raw_payload = self._io.read_bytes(self.payload_len)
                _io__raw_payload = KaitaiStream(BytesIO(self._raw_payload))
                self.payload = WatchpatPacket.MetricPayload(_io__raw_payload, self, self._root)
            elif _on == 1536:
                pass
                self._raw_payload = self._io.read_bytes(self.payload_len)
                _io__raw_payload = KaitaiStream(BytesIO(self._raw_payload))
                self.payload = WatchpatPacket.MotionPayload(_io__raw_payload, self, self._root)
            else:
                pass
                self._raw_payload = self._io.read_bytes(self.payload_len)
                _io__raw_payload = KaitaiStream(BytesIO(self._raw_payload))
                self.payload = WatchpatPacket.RawPayload(_io__raw_payload, self, self._root)


        def _fetch_instances(self):
            pass
            _on = self.record_id << 8 | self.record_type
            if _on == 1296:
                pass
                self.payload._fetch_instances()
            elif _on == 1536:
                pass
                self.payload._fetch_instances()
            else:
                pass
                self.payload._fetch_instances()


    class MetricPayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.MetricPayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.value = self._io.read_s4le()


        def _fetch_instances(self):
            pass


    class MotionPayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.MotionPayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.subframes = []
            i = 0
            while not self._io.is_eof():
                self.subframes.append(WatchpatPacket.MotionSubframe(self._io, self, self._root))
                i += 1



        def _fetch_instances(self):
            pass
            for i in range(len(self.subframes)):
                pass
                self.subframes[i]._fetch_instances()



    class MotionSubframe(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.MotionSubframe, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.marker = self._io.read_bytes(4)
            if not self.marker == b"\xDD\xDD\xA3\x57":
                raise kaitaistruct.ValidationNotEqualError(b"\xDD\xDD\xA3\x57", self.marker, self._io, u"/types/motion_subframe/seq/0")
            self.field_a = self._io.read_u2le()
            self.field_b = self._io.read_u2le()
            self.x = self._io.read_s2le()
            self.y = self._io.read_s2le()
            self.z = self._io.read_s2le()
            self.crc = self._io.read_u2le()


        def _fetch_instances(self):
            pass


    class PacketHeader(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.PacketHeader, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.signature = self._io.read_bytes(2)
            if not self.signature == b"\xBB\xBB":
                raise kaitaistruct.ValidationNotEqualError(b"\xBB\xBB", self.signature, self._io, u"/types/packet_header/seq/0")
            self.opcode = self._io.read_u2be()
            self.timestamp = self._io.read_u8le()
            self.packet_id = self._io.read_u4le()
            self.total_len = self._io.read_u2le()
            self.opcode_dependent = self._io.read_u2le()
            self.reserved = self._io.read_u2le()
            self.crc = self._io.read_u2le()


        def _fetch_instances(self):
            pass


    class RawPayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.RawPayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.data = self._io.read_bytes_full()


        def _fetch_instances(self):
            pass


    class SessionConfirmPayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.SessionConfirmPayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            pass


        def _fetch_instances(self):
            pass
            _ = self.device_subtype
            if hasattr(self, '_m_device_subtype'):
                pass

            _ = self.fw_version
            if hasattr(self, '_m_fw_version'):
                pass

            _ = self.hw_major
            if hasattr(self, '_m_hw_major'):
                pass

            _ = self.hw_minor
            if hasattr(self, '_m_hw_minor'):
                pass

            _ = self.pin_code_raw
            if hasattr(self, '_m_pin_code_raw'):
                pass

            _ = self.serial_number
            if hasattr(self, '_m_serial_number'):
                pass


        @property
        def device_subtype(self):
            if hasattr(self, '_m_device_subtype'):
                return self._m_device_subtype

            if self._io.size() >= 236:
                pass
                _pos = self._io.pos()
                self._io.seek(235)
                self._m_device_subtype = self._io.read_u1()
                self._io.seek(_pos)

            return getattr(self, '_m_device_subtype', None)

        @property
        def fw_version(self):
            if hasattr(self, '_m_fw_version'):
                return self._m_fw_version

            _pos = self._io.pos()
            self._io.seek(2)
            self._m_fw_version = self._io.read_u2le()
            self._io.seek(_pos)
            return getattr(self, '_m_fw_version', None)

        @property
        def has_finger_detection(self):
            if hasattr(self, '_m_has_finger_detection'):
                return self._m_has_finger_detection

            if self._io.size() >= 236:
                pass
                self._m_has_finger_detection = self.device_subtype & 8 != 0

            return getattr(self, '_m_has_finger_detection', None)

        @property
        def hw_major(self):
            if hasattr(self, '_m_hw_major'):
                return self._m_hw_major

            _pos = self._io.pos()
            self._io.seek(0)
            self._m_hw_major = self._io.read_u1()
            self._io.seek(_pos)
            return getattr(self, '_m_hw_major', None)

        @property
        def hw_minor(self):
            if hasattr(self, '_m_hw_minor'):
                return self._m_hw_minor

            _pos = self._io.pos()
            self._io.seek(1)
            self._m_hw_minor = self._io.read_u1()
            self._io.seek(_pos)
            return getattr(self, '_m_hw_minor', None)

        @property
        def is_wcp_less(self):
            if hasattr(self, '_m_is_wcp_less'):
                return self._m_is_wcp_less

            if self._io.size() >= 236:
                pass
                self._m_is_wcp_less = self.device_subtype & 85 != 0

            return getattr(self, '_m_is_wcp_less', None)

        @property
        def is_wp1m(self):
            if hasattr(self, '_m_is_wp1m'):
                return self._m_is_wp1m

            if self._io.size() >= 236:
                pass
                self._m_is_wp1m = self.device_subtype & 2 != 0

            return getattr(self, '_m_is_wp1m', None)

        @property
        def pin_code_raw(self):
            if hasattr(self, '_m_pin_code_raw'):
                return self._m_pin_code_raw

            if self._io.size() >= 223:
                pass
                _pos = self._io.pos()
                self._io.seek(221)
                self._m_pin_code_raw = self._io.read_u2le()
                self._io.seek(_pos)

            return getattr(self, '_m_pin_code_raw', None)

        @property
        def serial_number(self):
            if hasattr(self, '_m_serial_number'):
                return self._m_serial_number

            if self._io.size() >= 58:
                pass
                _pos = self._io.pos()
                self._io.seek(54)
                self._m_serial_number = self._io.read_u4le()
                self._io.seek(_pos)

            return getattr(self, '_m_serial_number', None)


    class TechStatusPayload(KaitaiStruct):
        def __init__(self, _io, _parent=None, _root=None):
            super(WatchpatPacket.TechStatusPayload, self).__init__(_io)
            self._parent = _parent
            self._root = _root
            self._read()

        def _read(self):
            self.battery_b0 = self._io.read_u1()
            self.battery_b1 = self._io.read_u1()
            self.vdd_b0 = self._io.read_u1()
            self.vdd_b1 = self._io.read_u1()
            self.ir_led_b0 = self._io.read_u1()
            self.ir_led_b1 = self._io.read_u1()
            self.red_led_b0 = self._io.read_u1()
            self.red_led_b1 = self._io.read_u1()
            self.pat_led_b0 = self._io.read_u1()
            self.pat_led_b1 = self._io.read_u1()


        def _fetch_instances(self):
            pass

        @property
        def battery_voltage(self):
            if hasattr(self, '_m_battery_voltage'):
                return self._m_battery_voltage

            self._m_battery_voltage = self.battery_b1 << 1 | self.battery_b0
            return getattr(self, '_m_battery_voltage', None)

        @property
        def ir_led(self):
            if hasattr(self, '_m_ir_led'):
                return self._m_ir_led

            self._m_ir_led = self.ir_led_b1 << 1 | self.ir_led_b0
            return getattr(self, '_m_ir_led', None)

        @property
        def pat_led(self):
            if hasattr(self, '_m_pat_led'):
                return self._m_pat_led

            self._m_pat_led = self.pat_led_b0 | self.pat_led_b1 << 1
            return getattr(self, '_m_pat_led', None)

        @property
        def red_led(self):
            if hasattr(self, '_m_red_led'):
                return self._m_red_led

            self._m_red_led = self.red_led_b1 << 1 | self.red_led_b0
            return getattr(self, '_m_red_led', None)

        @property
        def vdd_voltage(self):
            if hasattr(self, '_m_vdd_voltage'):
                return self._m_vdd_voltage

            self._m_vdd_voltage = self.vdd_b1 << 1 | self.vdd_b0
            return getattr(self, '_m_vdd_voltage', None)



