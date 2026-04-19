// This is a generated file! Please edit source .ksy file and use kaitai-struct-compiler to rebuild

package com.watchpat.recorder.kaitai;

import io.kaitai.struct.ByteBufferKaitaiStream;
import io.kaitai.struct.KaitaiStruct;
import io.kaitai.struct.KaitaiStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Arrays;

public class WatchpatPacket extends KaitaiStruct {
    public static WatchpatPacket fromFile(String fileName) throws IOException {
        return new WatchpatPacket(new ByteBufferKaitaiStream(fileName));
    }

    public WatchpatPacket(KaitaiStream _io) {
        this(_io, null, null);
    }

    public WatchpatPacket(KaitaiStream _io, KaitaiStruct _parent) {
        this(_io, _parent, null);
    }

    public WatchpatPacket(KaitaiStream _io, KaitaiStruct _parent, WatchpatPacket _root) {
        super(_io);
        this._parent = _parent;
        this._root = _root == null ? this : _root;
        _read();
    }
    private void _read() {
        this.header = new PacketHeader(this._io, this, _root);
        int _bodyLen = header().totalLen() - 24;
        switch (header().opcode()) {
        case 1280: {
            this._rawBody = this._io.readBytes(_bodyLen);
            this.body = new SessionConfirmPayload(new ByteBufferKaitaiStream(this._rawBody), this, _root);
            break;
        }
        case 2048: {
            this._rawBody = this._io.readBytes(_bodyLen);
            this.body = new DataPacketPayload(new ByteBufferKaitaiStream(this._rawBody), this, _root);
            break;
        }
        case 4864: {
            this._rawBody = this._io.readBytes(_bodyLen);
            this.body = new BitResponsePayload(new ByteBufferKaitaiStream(this._rawBody), this, _root);
            break;
        }
        case 512: {
            this._rawBody = this._io.readBytes(_bodyLen);
            this.body = new SessionConfirmPayload(new ByteBufferKaitaiStream(this._rawBody), this, _root);
            break;
        }
        case 5632: {
            this._rawBody = this._io.readBytes(_bodyLen);
            this.body = new TechStatusPayload(new ByteBufferKaitaiStream(this._rawBody), this, _root);
            break;
        }
        default: {
            this._rawBody = this._io.readBytes(_bodyLen);
            this.body = new RawPayload(new ByteBufferKaitaiStream(this._rawBody), this, _root);
            break;
        }
        }
    }

    public void _fetchInstances() {
        this.header._fetchInstances();
        switch (header().opcode()) {
        case 1280: {
            ((SessionConfirmPayload) (this.body))._fetchInstances();
            break;
        }
        case 2048: {
            ((DataPacketPayload) (this.body))._fetchInstances();
            break;
        }
        case 4864: {
            ((BitResponsePayload) (this.body))._fetchInstances();
            break;
        }
        case 512: {
            ((SessionConfirmPayload) (this.body))._fetchInstances();
            break;
        }
        case 5632: {
            ((TechStatusPayload) (this.body))._fetchInstances();
            break;
        }
        default: {
            ((RawPayload) (this.body))._fetchInstances();
            break;
        }
        }
    }
    public static class BitResponsePayload extends KaitaiStruct {
        public static BitResponsePayload fromFile(String fileName) throws IOException {
            return new BitResponsePayload(new ByteBufferKaitaiStream(fileName));
        }

        public BitResponsePayload(KaitaiStream _io) {
            this(_io, null, null);
        }

        public BitResponsePayload(KaitaiStream _io, WatchpatPacket _parent) {
            this(_io, _parent, null);
        }

        public BitResponsePayload(KaitaiStream _io, WatchpatPacket _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.rawFlags = this._io.readU4le();
        }

        public void _fetchInstances() {
        }
        private Boolean actigraphError;
        public Boolean actigraphError() {
            if (this.actigraphError != null)
                return this.actigraphError;
            this.actigraphError = (rawFlags() & 4) != 0;
            return this.actigraphError;
        }
        private Boolean batteryDepleted;
        public Boolean batteryDepleted() {
            if (this.batteryDepleted != null)
                return this.batteryDepleted;
            this.batteryDepleted = (rawFlags() & 1) != 0;
            return this.batteryDepleted;
        }
        private Boolean batteryLow;
        public Boolean batteryLow() {
            if (this.batteryLow != null)
                return this.batteryLow;
            this.batteryLow = (rawFlags() & 2) != 0;
            return this.batteryLow;
        }
        private Boolean flashError;
        public Boolean flashError() {
            if (this.flashError != null)
                return this.flashError;
            this.flashError = (rawFlags() & 64) != 0;
            return this.flashError;
        }
        private Boolean nafError;
        public Boolean nafError() {
            if (this.nafError != null)
                return this.nafError;
            this.nafError = (rawFlags() & 8) != 0;
            return this.nafError;
        }
        private Boolean probeFailure;
        public Boolean probeFailure() {
            if (this.probeFailure != null)
                return this.probeFailure;
            this.probeFailure = (rawFlags() & 512) != 0;
            return this.probeFailure;
        }
        private Boolean probeLedError;
        public Boolean probeLedError() {
            if (this.probeLedError != null)
                return this.probeLedError;
            this.probeLedError = (rawFlags() & 128) != 0;
            return this.probeLedError;
        }
        private Boolean probePhotoError;
        public Boolean probePhotoError() {
            if (this.probePhotoError != null)
                return this.probePhotoError;
            this.probePhotoError = (rawFlags() & 256) != 0;
            return this.probePhotoError;
        }
        private Boolean spbError;
        public Boolean spbError() {
            if (this.spbError != null)
                return this.spbError;
            this.spbError = (rawFlags() & 1024) != 0;
            return this.spbError;
        }
        private Boolean usedDevice;
        public Boolean usedDevice() {
            if (this.usedDevice != null)
                return this.usedDevice;
            this.usedDevice = (rawFlags() & 32) != 0;
            return this.usedDevice;
        }
        private Boolean vddError;
        public Boolean vddError() {
            if (this.vddError != null)
                return this.vddError;
            this.vddError = (rawFlags() & 16) != 0;
            return this.vddError;
        }
        private long rawFlags;
        private WatchpatPacket _root;
        private WatchpatPacket _parent;
        public long rawFlags() { return rawFlags; }
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket _parent() { return _parent; }
    }
    public static class DataPacketPayload extends KaitaiStruct {
        public static DataPacketPayload fromFile(String fileName) throws IOException {
            return new DataPacketPayload(new ByteBufferKaitaiStream(fileName));
        }

        public DataPacketPayload(KaitaiStream _io) {
            this(_io, null, null);
        }

        public DataPacketPayload(KaitaiStream _io, WatchpatPacket _parent) {
            this(_io, _parent, null);
        }

        public DataPacketPayload(KaitaiStream _io, WatchpatPacket _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.recordCount = this._io.readU1();
            this.subHeader = this._io.readU2le();
            this.records = new ArrayList<LogicalRecord>();
            {
                int i = 0;
                while (!this._io.isEof()) {
                    this.records.add(new LogicalRecord(this._io, this, _root));
                    i++;
                }
            }
        }

        public void _fetchInstances() {
            for (int i = 0; i < this.records.size(); i++) {
                this.records.get(((Number) (i)).intValue())._fetchInstances();
            }
        }
        private int recordCount;
        private int subHeader;
        private List<LogicalRecord> records;
        private WatchpatPacket _root;
        private WatchpatPacket _parent;
        public int recordCount() { return recordCount; }
        public int subHeader() { return subHeader; }
        public List<LogicalRecord> records() { return records; }
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket _parent() { return _parent; }
    }
    public static class LogicalRecord extends KaitaiStruct {
        public static LogicalRecord fromFile(String fileName) throws IOException {
            return new LogicalRecord(new ByteBufferKaitaiStream(fileName));
        }

        public LogicalRecord(KaitaiStream _io) {
            this(_io, null, null);
        }

        public LogicalRecord(KaitaiStream _io, WatchpatPacket.DataPacketPayload _parent) {
            this(_io, _parent, null);
        }

        public LogicalRecord(KaitaiStream _io, WatchpatPacket.DataPacketPayload _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.sync = this._io.readBytes(2);
            if (!(Arrays.equals(this.sync, new byte[] { -86, -86 }))) {
                throw new KaitaiStream.ValidationNotEqualError(new byte[] { -86, -86 }, this.sync, this._io, "/types/logical_record/seq/0");
            }
            this.recordId = this._io.readU1();
            this.recordType = this._io.readU1();
            this.payloadLen = this._io.readU2le();
            this.rate = this._io.readU2le();
            this.flags = this._io.readU4le();
            this._rawPayload = this._io.readBytes(payloadLen());
            switch (recordId() << 8 | recordType()) {
            case 1296: {
                this.payload = new MetricPayload(new ByteBufferKaitaiStream(this._rawPayload), this, _root);
                break;
            }
            case 1536: {
                this.payload = new MotionPayload(new ByteBufferKaitaiStream(this._rawPayload), this, _root);
                break;
            }
            default: {
                this.payload = new RawPayload(new ByteBufferKaitaiStream(this._rawPayload), this, _root);
                break;
            }
            }
        }

        public void _fetchInstances() {
            switch (recordId() << 8 | recordType()) {
            case 1296: {
                ((MetricPayload) (this.payload))._fetchInstances();
                break;
            }
            case 1536: {
                ((MotionPayload) (this.payload))._fetchInstances();
                break;
            }
            default: {
                ((RawPayload) (this.payload))._fetchInstances();
                break;
            }
            }
        }
        private byte[] sync;
        private int recordId;
        private int recordType;
        private int payloadLen;
        private int rate;
        private long flags;
        private byte[] _rawPayload;
        private KaitaiStruct payload;
        private WatchpatPacket _root;
        private WatchpatPacket.DataPacketPayload _parent;
        public byte[] sync() { return sync; }
        public int recordId() { return recordId; }
        public int recordType() { return recordType; }
        public int payloadLen() { return payloadLen; }
        public int rate() { return rate; }
        public long flags() { return flags; }
        public byte[] rawPayload() { return _rawPayload; }
        public KaitaiStruct payload() { return payload; }
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket.DataPacketPayload _parent() { return _parent; }
    }
    public static class MetricPayload extends KaitaiStruct {
        public static MetricPayload fromFile(String fileName) throws IOException {
            return new MetricPayload(new ByteBufferKaitaiStream(fileName));
        }

        public MetricPayload(KaitaiStream _io) {
            this(_io, null, null);
        }

        public MetricPayload(KaitaiStream _io, WatchpatPacket.LogicalRecord _parent) {
            this(_io, _parent, null);
        }

        public MetricPayload(KaitaiStream _io, WatchpatPacket.LogicalRecord _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.value = this._io.readS4le();
        }

        public void _fetchInstances() {
        }
        private int value;
        private WatchpatPacket _root;
        private WatchpatPacket.LogicalRecord _parent;
        public int value() { return value; }
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket.LogicalRecord _parent() { return _parent; }
    }
    public static class MotionPayload extends KaitaiStruct {
        public static MotionPayload fromFile(String fileName) throws IOException {
            return new MotionPayload(new ByteBufferKaitaiStream(fileName));
        }

        public MotionPayload(KaitaiStream _io) {
            this(_io, null, null);
        }

        public MotionPayload(KaitaiStream _io, WatchpatPacket.LogicalRecord _parent) {
            this(_io, _parent, null);
        }

        public MotionPayload(KaitaiStream _io, WatchpatPacket.LogicalRecord _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.subframes = new ArrayList<MotionSubframe>();
            {
                int i = 0;
                while (!this._io.isEof()) {
                    this.subframes.add(new MotionSubframe(this._io, this, _root));
                    i++;
                }
            }
        }

        public void _fetchInstances() {
            for (int i = 0; i < this.subframes.size(); i++) {
                this.subframes.get(((Number) (i)).intValue())._fetchInstances();
            }
        }
        private List<MotionSubframe> subframes;
        private WatchpatPacket _root;
        private WatchpatPacket.LogicalRecord _parent;
        public List<MotionSubframe> subframes() { return subframes; }
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket.LogicalRecord _parent() { return _parent; }
    }
    public static class MotionSubframe extends KaitaiStruct {
        public static MotionSubframe fromFile(String fileName) throws IOException {
            return new MotionSubframe(new ByteBufferKaitaiStream(fileName));
        }

        public MotionSubframe(KaitaiStream _io) {
            this(_io, null, null);
        }

        public MotionSubframe(KaitaiStream _io, WatchpatPacket.MotionPayload _parent) {
            this(_io, _parent, null);
        }

        public MotionSubframe(KaitaiStream _io, WatchpatPacket.MotionPayload _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.marker = this._io.readBytes(4);
            if (!(Arrays.equals(this.marker, new byte[] { -35, -35, -93, 87 }))) {
                throw new KaitaiStream.ValidationNotEqualError(new byte[] { -35, -35, -93, 87 }, this.marker, this._io, "/types/motion_subframe/seq/0");
            }
            this.fieldA = this._io.readU2le();
            this.fieldB = this._io.readU2le();
            this.x = this._io.readS2le();
            this.y = this._io.readS2le();
            this.z = this._io.readS2le();
            this.crc = this._io.readU2le();
        }

        public void _fetchInstances() {
        }
        private byte[] marker;
        private int fieldA;
        private int fieldB;
        private short x;
        private short y;
        private short z;
        private int crc;
        private WatchpatPacket _root;
        private WatchpatPacket.MotionPayload _parent;
        public byte[] marker() { return marker; }
        public int fieldA() { return fieldA; }
        public int fieldB() { return fieldB; }
        public short x() { return x; }
        public short y() { return y; }
        public short z() { return z; }
        public int crc() { return crc; }
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket.MotionPayload _parent() { return _parent; }
    }
    public static class PacketHeader extends KaitaiStruct {
        public static PacketHeader fromFile(String fileName) throws IOException {
            return new PacketHeader(new ByteBufferKaitaiStream(fileName));
        }

        public PacketHeader(KaitaiStream _io) {
            this(_io, null, null);
        }

        public PacketHeader(KaitaiStream _io, WatchpatPacket _parent) {
            this(_io, _parent, null);
        }

        public PacketHeader(KaitaiStream _io, WatchpatPacket _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.signature = this._io.readBytes(2);
            if (!(Arrays.equals(this.signature, new byte[] { -69, -69 }))) {
                throw new KaitaiStream.ValidationNotEqualError(new byte[] { -69, -69 }, this.signature, this._io, "/types/packet_header/seq/0");
            }
            this.opcode = this._io.readU2be();
            this.timestamp = this._io.readU8le();
            this.packetId = this._io.readU4le();
            this.totalLen = this._io.readU2le();
            this.opcodeDependent = this._io.readU2le();
            this.reserved = this._io.readU2le();
            this.crc = this._io.readU2le();
        }

        public void _fetchInstances() {
        }
        private byte[] signature;
        private int opcode;
        private long timestamp;
        private long packetId;
        private int totalLen;
        private int opcodeDependent;
        private int reserved;
        private int crc;
        private WatchpatPacket _root;
        private WatchpatPacket _parent;
        public byte[] signature() { return signature; }
        public int opcode() { return opcode; }
        public long timestamp() { return timestamp; }
        public long packetId() { return packetId; }
        public int totalLen() { return totalLen; }
        public int opcodeDependent() { return opcodeDependent; }
        public int reserved() { return reserved; }
        public int crc() { return crc; }
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket _parent() { return _parent; }
    }
    public static class RawPayload extends KaitaiStruct {
        public static RawPayload fromFile(String fileName) throws IOException {
            return new RawPayload(new ByteBufferKaitaiStream(fileName));
        }

        public RawPayload(KaitaiStream _io) {
            this(_io, null, null);
        }

        public RawPayload(KaitaiStream _io, KaitaiStruct _parent) {
            this(_io, _parent, null);
        }

        public RawPayload(KaitaiStream _io, KaitaiStruct _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.data = this._io.readBytesFull();
        }

        public void _fetchInstances() {
        }
        private byte[] data;
        private WatchpatPacket _root;
        private KaitaiStruct _parent;
        public byte[] data() { return data; }
        public WatchpatPacket _root() { return _root; }
        public KaitaiStruct _parent() { return _parent; }
    }
    public static class SessionConfirmPayload extends KaitaiStruct {
        public static SessionConfirmPayload fromFile(String fileName) throws IOException {
            return new SessionConfirmPayload(new ByteBufferKaitaiStream(fileName));
        }

        public SessionConfirmPayload(KaitaiStream _io) {
            this(_io, null, null);
        }

        public SessionConfirmPayload(KaitaiStream _io, WatchpatPacket _parent) {
            this(_io, _parent, null);
        }

        public SessionConfirmPayload(KaitaiStream _io, WatchpatPacket _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
        }

        public void _fetchInstances() {
            deviceSubtype();
            if (this.deviceSubtype != null) {
            }
            fwVersion();
            if (this.fwVersion != null) {
            }
            hwMajor();
            if (this.hwMajor != null) {
            }
            hwMinor();
            if (this.hwMinor != null) {
            }
            pinCodeRaw();
            if (this.pinCodeRaw != null) {
            }
            serialNumber();
            if (this.serialNumber != null) {
            }
        }
        private Integer deviceSubtype;
        public Integer deviceSubtype() {
            if (this.deviceSubtype != null)
                return this.deviceSubtype;
            if (_io().size() >= 236) {
                long _pos = this._io.pos();
                this._io.seek(235);
                this.deviceSubtype = this._io.readU1();
                this._io.seek(_pos);
            }
            return this.deviceSubtype;
        }
        private Integer fwVersion;
        public Integer fwVersion() {
            if (this.fwVersion != null)
                return this.fwVersion;
            long _pos = this._io.pos();
            this._io.seek(2);
            this.fwVersion = this._io.readU2le();
            this._io.seek(_pos);
            return this.fwVersion;
        }
        private Boolean hasFingerDetection;
        public Boolean hasFingerDetection() {
            if (this.hasFingerDetection != null)
                return this.hasFingerDetection;
            if (_io().size() >= 236) {
                this.hasFingerDetection = (deviceSubtype() & 8) != 0;
            }
            return this.hasFingerDetection;
        }
        private Integer hwMajor;
        public Integer hwMajor() {
            if (this.hwMajor != null)
                return this.hwMajor;
            long _pos = this._io.pos();
            this._io.seek(0);
            this.hwMajor = this._io.readU1();
            this._io.seek(_pos);
            return this.hwMajor;
        }
        private Integer hwMinor;
        public Integer hwMinor() {
            if (this.hwMinor != null)
                return this.hwMinor;
            long _pos = this._io.pos();
            this._io.seek(1);
            this.hwMinor = this._io.readU1();
            this._io.seek(_pos);
            return this.hwMinor;
        }
        private Boolean isWcpLess;
        public Boolean isWcpLess() {
            if (this.isWcpLess != null)
                return this.isWcpLess;
            if (_io().size() >= 236) {
                this.isWcpLess = (deviceSubtype() & 85) != 0;
            }
            return this.isWcpLess;
        }
        private Boolean isWp1m;
        public Boolean isWp1m() {
            if (this.isWp1m != null)
                return this.isWp1m;
            if (_io().size() >= 236) {
                this.isWp1m = (deviceSubtype() & 2) != 0;
            }
            return this.isWp1m;
        }
        private Integer pinCodeRaw;
        public Integer pinCodeRaw() {
            if (this.pinCodeRaw != null)
                return this.pinCodeRaw;
            if (_io().size() >= 223) {
                long _pos = this._io.pos();
                this._io.seek(221);
                this.pinCodeRaw = this._io.readU2le();
                this._io.seek(_pos);
            }
            return this.pinCodeRaw;
        }
        private Long serialNumber;
        public Long serialNumber() {
            if (this.serialNumber != null)
                return this.serialNumber;
            if (_io().size() >= 58) {
                long _pos = this._io.pos();
                this._io.seek(54);
                this.serialNumber = this._io.readU4le();
                this._io.seek(_pos);
            }
            return this.serialNumber;
        }
        private WatchpatPacket _root;
        private WatchpatPacket _parent;
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket _parent() { return _parent; }
    }
    public static class TechStatusPayload extends KaitaiStruct {
        public static TechStatusPayload fromFile(String fileName) throws IOException {
            return new TechStatusPayload(new ByteBufferKaitaiStream(fileName));
        }

        public TechStatusPayload(KaitaiStream _io) {
            this(_io, null, null);
        }

        public TechStatusPayload(KaitaiStream _io, WatchpatPacket _parent) {
            this(_io, _parent, null);
        }

        public TechStatusPayload(KaitaiStream _io, WatchpatPacket _parent, WatchpatPacket _root) {
            super(_io);
            this._parent = _parent;
            this._root = _root;
            _read();
        }
        private void _read() {
            this.batteryB0 = this._io.readU1();
            this.batteryB1 = this._io.readU1();
            this.vddB0 = this._io.readU1();
            this.vddB1 = this._io.readU1();
            this.irLedB0 = this._io.readU1();
            this.irLedB1 = this._io.readU1();
            this.redLedB0 = this._io.readU1();
            this.redLedB1 = this._io.readU1();
            this.patLedB0 = this._io.readU1();
            this.patLedB1 = this._io.readU1();
        }

        public void _fetchInstances() {
        }
        private Integer batteryVoltage;
        public Integer batteryVoltage() {
            if (this.batteryVoltage != null)
                return this.batteryVoltage;
            this.batteryVoltage = ((Number) (batteryB1() << 1 | batteryB0())).intValue();
            return this.batteryVoltage;
        }
        private Integer irLed;
        public Integer irLed() {
            if (this.irLed != null)
                return this.irLed;
            this.irLed = ((Number) (irLedB1() << 1 | irLedB0())).intValue();
            return this.irLed;
        }
        private Integer patLed;
        public Integer patLed() {
            if (this.patLed != null)
                return this.patLed;
            this.patLed = ((Number) (patLedB0() | patLedB1() << 1)).intValue();
            return this.patLed;
        }
        private Integer redLed;
        public Integer redLed() {
            if (this.redLed != null)
                return this.redLed;
            this.redLed = ((Number) (redLedB1() << 1 | redLedB0())).intValue();
            return this.redLed;
        }
        private Integer vddVoltage;
        public Integer vddVoltage() {
            if (this.vddVoltage != null)
                return this.vddVoltage;
            this.vddVoltage = ((Number) (vddB1() << 1 | vddB0())).intValue();
            return this.vddVoltage;
        }
        private int batteryB0;
        private int batteryB1;
        private int vddB0;
        private int vddB1;
        private int irLedB0;
        private int irLedB1;
        private int redLedB0;
        private int redLedB1;
        private int patLedB0;
        private int patLedB1;
        private WatchpatPacket _root;
        private WatchpatPacket _parent;
        public int batteryB0() { return batteryB0; }
        public int batteryB1() { return batteryB1; }
        public int vddB0() { return vddB0; }
        public int vddB1() { return vddB1; }
        public int irLedB0() { return irLedB0; }
        public int irLedB1() { return irLedB1; }
        public int redLedB0() { return redLedB0; }
        public int redLedB1() { return redLedB1; }
        public int patLedB0() { return patLedB0; }
        public int patLedB1() { return patLedB1; }
        public WatchpatPacket _root() { return _root; }
        public WatchpatPacket _parent() { return _parent; }
    }
    private PacketHeader header;
    private KaitaiStruct body;
    private byte[] _rawBody;
    private WatchpatPacket _root;
    private KaitaiStruct _parent;
    public PacketHeader header() { return header; }
    public KaitaiStruct body() { return body; }
    public byte[] rawBody() { return _rawBody; }
    public WatchpatPacket _root() { return _root; }
    public KaitaiStruct _parent() { return _parent; }
}
