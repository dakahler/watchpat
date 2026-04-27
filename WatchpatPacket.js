// This is a generated file! Please edit source .ksy file and use kaitai-struct-compiler to rebuild

(function (root, factory) {
  if (typeof define === 'function' && define.amd) {
    define(['exports', 'kaitai-struct/KaitaiStream'], factory);
  } else if (typeof exports === 'object' && exports !== null && typeof exports.nodeType !== 'number') {
    factory(exports, require('kaitai-struct/KaitaiStream'));
  } else {
    factory(root.WatchpatPacket || (root.WatchpatPacket = {}), root.KaitaiStream);
  }
})(typeof self !== 'undefined' ? self : this, function (WatchpatPacket_, KaitaiStream) {
var WatchpatPacket = (function() {
  function WatchpatPacket(_io, _parent, _root) {
    this._io = _io;
    this._parent = _parent;
    this._root = _root || this;

    this._read();
  }
  WatchpatPacket.prototype._read = function() {
    this.header = new PacketHeader(this._io, this, this._root);
    switch (this.header.opcode) {
    case 1280:
      this._raw_body = this._io.readBytes(this.header.totalLen - 24);
      var _io__raw_body = new KaitaiStream(this._raw_body);
      this.body = new SessionConfirmPayload(_io__raw_body, this, this._root);
      break;
    case 2048:
      this._raw_body = this._io.readBytes(this.header.totalLen - 24);
      var _io__raw_body = new KaitaiStream(this._raw_body);
      this.body = new DataPacketPayload(_io__raw_body, this, this._root);
      break;
    case 4864:
      this._raw_body = this._io.readBytes(this.header.totalLen - 24);
      var _io__raw_body = new KaitaiStream(this._raw_body);
      this.body = new BitResponsePayload(_io__raw_body, this, this._root);
      break;
    case 512:
      this._raw_body = this._io.readBytes(this.header.totalLen - 24);
      var _io__raw_body = new KaitaiStream(this._raw_body);
      this.body = new SessionConfirmPayload(_io__raw_body, this, this._root);
      break;
    case 5632:
      this._raw_body = this._io.readBytes(this.header.totalLen - 24);
      var _io__raw_body = new KaitaiStream(this._raw_body);
      this.body = new TechStatusPayload(_io__raw_body, this, this._root);
      break;
    default:
      this._raw_body = this._io.readBytes(this.header.totalLen - 24);
      var _io__raw_body = new KaitaiStream(this._raw_body);
      this.body = new RawPayload(_io__raw_body, this, this._root);
      break;
    }
  }

  var BitResponsePayload = WatchpatPacket.BitResponsePayload = (function() {
    function BitResponsePayload(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    BitResponsePayload.prototype._read = function() {
      this.rawFlags = this._io.readU4le();
    }
    Object.defineProperty(BitResponsePayload.prototype, 'actigraphError', {
      get: function() {
        if (this._m_actigraphError !== undefined)
          return this._m_actigraphError;
        this._m_actigraphError = (this.rawFlags & 4) != 0;
        return this._m_actigraphError;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'batteryDepleted', {
      get: function() {
        if (this._m_batteryDepleted !== undefined)
          return this._m_batteryDepleted;
        this._m_batteryDepleted = (this.rawFlags & 1) != 0;
        return this._m_batteryDepleted;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'batteryLow', {
      get: function() {
        if (this._m_batteryLow !== undefined)
          return this._m_batteryLow;
        this._m_batteryLow = (this.rawFlags & 2) != 0;
        return this._m_batteryLow;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'flashError', {
      get: function() {
        if (this._m_flashError !== undefined)
          return this._m_flashError;
        this._m_flashError = (this.rawFlags & 64) != 0;
        return this._m_flashError;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'nafError', {
      get: function() {
        if (this._m_nafError !== undefined)
          return this._m_nafError;
        this._m_nafError = (this.rawFlags & 8) != 0;
        return this._m_nafError;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'probeFailure', {
      get: function() {
        if (this._m_probeFailure !== undefined)
          return this._m_probeFailure;
        this._m_probeFailure = (this.rawFlags & 512) != 0;
        return this._m_probeFailure;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'probeLedError', {
      get: function() {
        if (this._m_probeLedError !== undefined)
          return this._m_probeLedError;
        this._m_probeLedError = (this.rawFlags & 128) != 0;
        return this._m_probeLedError;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'probePhotoError', {
      get: function() {
        if (this._m_probePhotoError !== undefined)
          return this._m_probePhotoError;
        this._m_probePhotoError = (this.rawFlags & 256) != 0;
        return this._m_probePhotoError;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'spbError', {
      get: function() {
        if (this._m_spbError !== undefined)
          return this._m_spbError;
        this._m_spbError = (this.rawFlags & 1024) != 0;
        return this._m_spbError;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'usedDevice', {
      get: function() {
        if (this._m_usedDevice !== undefined)
          return this._m_usedDevice;
        this._m_usedDevice = (this.rawFlags & 32) != 0;
        return this._m_usedDevice;
      }
    });
    Object.defineProperty(BitResponsePayload.prototype, 'vddError', {
      get: function() {
        if (this._m_vddError !== undefined)
          return this._m_vddError;
        this._m_vddError = (this.rawFlags & 16) != 0;
        return this._m_vddError;
      }
    });

    return BitResponsePayload;
  })();

  var DataPacketPayload = WatchpatPacket.DataPacketPayload = (function() {
    function DataPacketPayload(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    DataPacketPayload.prototype._read = function() {
      this.recordCount = this._io.readU1();
      this.subHeader = this._io.readU2le();
      this.records = [];
      var i = 0;
      while (!this._io.isEof()) {
        this.records.push(new LogicalRecord(this._io, this, this._root));
        i++;
      }
    }

    return DataPacketPayload;
  })();

  var LogicalRecord = WatchpatPacket.LogicalRecord = (function() {
    function LogicalRecord(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    LogicalRecord.prototype._read = function() {
      this.sync = this._io.readBytes(2);
      if (!((KaitaiStream.byteArrayCompare(this.sync, new Uint8Array([170, 170])) == 0))) {
        throw new KaitaiStream.ValidationNotEqualError(new Uint8Array([170, 170]), this.sync, this._io, "/types/logical_record/seq/0");
      }
      this.recordId = this._io.readU1();
      this.recordType = this._io.readU1();
      this.payloadLen = this._io.readU2le();
      this.rate = this._io.readU2le();
      this.flags = this._io.readU4le();
      switch (this.recordId << 8 | this.recordType) {
      case 1296:
        this._raw_payload = this._io.readBytes(this.payloadLen);
        var _io__raw_payload = new KaitaiStream(this._raw_payload);
        this.payload = new MetricPayload(_io__raw_payload, this, this._root);
        break;
      case 1536:
        this._raw_payload = this._io.readBytes(this.payloadLen);
        var _io__raw_payload = new KaitaiStream(this._raw_payload);
        this.payload = new MotionPayload(_io__raw_payload, this, this._root);
        break;
      default:
        this._raw_payload = this._io.readBytes(this.payloadLen);
        var _io__raw_payload = new KaitaiStream(this._raw_payload);
        this.payload = new RawPayload(_io__raw_payload, this, this._root);
        break;
      }
    }

    return LogicalRecord;
  })();

  var MetricPayload = WatchpatPacket.MetricPayload = (function() {
    function MetricPayload(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    MetricPayload.prototype._read = function() {
      this.value = this._io.readS4le();
    }

    return MetricPayload;
  })();

  var MotionPayload = WatchpatPacket.MotionPayload = (function() {
    function MotionPayload(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    MotionPayload.prototype._read = function() {
      this.subframes = [];
      var i = 0;
      while (!this._io.isEof()) {
        this.subframes.push(new MotionSubframe(this._io, this, this._root));
        i++;
      }
    }

    return MotionPayload;
  })();

  var MotionSubframe = WatchpatPacket.MotionSubframe = (function() {
    function MotionSubframe(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    MotionSubframe.prototype._read = function() {
      this.marker = this._io.readBytes(4);
      if (!((KaitaiStream.byteArrayCompare(this.marker, new Uint8Array([221, 221, 163, 87])) == 0))) {
        throw new KaitaiStream.ValidationNotEqualError(new Uint8Array([221, 221, 163, 87]), this.marker, this._io, "/types/motion_subframe/seq/0");
      }
      this.fieldA = this._io.readU2le();
      this.fieldB = this._io.readU2le();
      this.x = this._io.readS2le();
      this.y = this._io.readS2le();
      this.z = this._io.readS2le();
      this.crc = this._io.readU2le();
    }

    return MotionSubframe;
  })();

  var PacketHeader = WatchpatPacket.PacketHeader = (function() {
    function PacketHeader(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    PacketHeader.prototype._read = function() {
      this.signature = this._io.readBytes(2);
      if (!((KaitaiStream.byteArrayCompare(this.signature, new Uint8Array([187, 187])) == 0))) {
        throw new KaitaiStream.ValidationNotEqualError(new Uint8Array([187, 187]), this.signature, this._io, "/types/packet_header/seq/0");
      }
      this.opcode = this._io.readU2be();
      this.timestamp = this._io.readU8le();
      this.packetId = this._io.readU4le();
      this.totalLen = this._io.readU2le();
      this.opcodeDependent = this._io.readU2le();
      this.reserved = this._io.readU2le();
      this.crc = this._io.readU2le();
    }

    return PacketHeader;
  })();

  var RawPayload = WatchpatPacket.RawPayload = (function() {
    function RawPayload(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    RawPayload.prototype._read = function() {
      this.data = this._io.readBytesFull();
    }

    return RawPayload;
  })();

  var SessionConfirmPayload = WatchpatPacket.SessionConfirmPayload = (function() {
    function SessionConfirmPayload(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    SessionConfirmPayload.prototype._read = function() {
    }
    Object.defineProperty(SessionConfirmPayload.prototype, 'deviceSubtype', {
      get: function() {
        if (this._m_deviceSubtype !== undefined)
          return this._m_deviceSubtype;
        if (this._io.size >= 236) {
          var _pos = this._io.pos;
          this._io.seek(235);
          this._m_deviceSubtype = this._io.readU1();
          this._io.seek(_pos);
        }
        return this._m_deviceSubtype;
      }
    });
    Object.defineProperty(SessionConfirmPayload.prototype, 'fwVersion', {
      get: function() {
        if (this._m_fwVersion !== undefined)
          return this._m_fwVersion;
        var _pos = this._io.pos;
        this._io.seek(2);
        this._m_fwVersion = this._io.readU2le();
        this._io.seek(_pos);
        return this._m_fwVersion;
      }
    });
    Object.defineProperty(SessionConfirmPayload.prototype, 'hasFingerDetection', {
      get: function() {
        if (this._m_hasFingerDetection !== undefined)
          return this._m_hasFingerDetection;
        if (this._io.size >= 236) {
          this._m_hasFingerDetection = (this.deviceSubtype & 8) != 0;
        }
        return this._m_hasFingerDetection;
      }
    });
    Object.defineProperty(SessionConfirmPayload.prototype, 'hwMajor', {
      get: function() {
        if (this._m_hwMajor !== undefined)
          return this._m_hwMajor;
        var _pos = this._io.pos;
        this._io.seek(0);
        this._m_hwMajor = this._io.readU1();
        this._io.seek(_pos);
        return this._m_hwMajor;
      }
    });
    Object.defineProperty(SessionConfirmPayload.prototype, 'hwMinor', {
      get: function() {
        if (this._m_hwMinor !== undefined)
          return this._m_hwMinor;
        var _pos = this._io.pos;
        this._io.seek(1);
        this._m_hwMinor = this._io.readU1();
        this._io.seek(_pos);
        return this._m_hwMinor;
      }
    });
    Object.defineProperty(SessionConfirmPayload.prototype, 'isWcpLess', {
      get: function() {
        if (this._m_isWcpLess !== undefined)
          return this._m_isWcpLess;
        if (this._io.size >= 236) {
          this._m_isWcpLess = (this.deviceSubtype & 85) != 0;
        }
        return this._m_isWcpLess;
      }
    });
    Object.defineProperty(SessionConfirmPayload.prototype, 'isWp1m', {
      get: function() {
        if (this._m_isWp1m !== undefined)
          return this._m_isWp1m;
        if (this._io.size >= 236) {
          this._m_isWp1m = (this.deviceSubtype & 2) != 0;
        }
        return this._m_isWp1m;
      }
    });
    Object.defineProperty(SessionConfirmPayload.prototype, 'pinCodeRaw', {
      get: function() {
        if (this._m_pinCodeRaw !== undefined)
          return this._m_pinCodeRaw;
        if (this._io.size >= 223) {
          var _pos = this._io.pos;
          this._io.seek(221);
          this._m_pinCodeRaw = this._io.readU2le();
          this._io.seek(_pos);
        }
        return this._m_pinCodeRaw;
      }
    });
    Object.defineProperty(SessionConfirmPayload.prototype, 'serialNumber', {
      get: function() {
        if (this._m_serialNumber !== undefined)
          return this._m_serialNumber;
        if (this._io.size >= 58) {
          var _pos = this._io.pos;
          this._io.seek(54);
          this._m_serialNumber = this._io.readU4le();
          this._io.seek(_pos);
        }
        return this._m_serialNumber;
      }
    });

    return SessionConfirmPayload;
  })();

  var TechStatusPayload = WatchpatPacket.TechStatusPayload = (function() {
    function TechStatusPayload(_io, _parent, _root) {
      this._io = _io;
      this._parent = _parent;
      this._root = _root;

      this._read();
    }
    TechStatusPayload.prototype._read = function() {
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
    Object.defineProperty(TechStatusPayload.prototype, 'batteryVoltage', {
      get: function() {
        if (this._m_batteryVoltage !== undefined)
          return this._m_batteryVoltage;
        this._m_batteryVoltage = this.batteryB1 << 1 | this.batteryB0;
        return this._m_batteryVoltage;
      }
    });
    Object.defineProperty(TechStatusPayload.prototype, 'irLed', {
      get: function() {
        if (this._m_irLed !== undefined)
          return this._m_irLed;
        this._m_irLed = this.irLedB1 << 1 | this.irLedB0;
        return this._m_irLed;
      }
    });
    Object.defineProperty(TechStatusPayload.prototype, 'patLed', {
      get: function() {
        if (this._m_patLed !== undefined)
          return this._m_patLed;
        this._m_patLed = this.patLedB0 | this.patLedB1 << 1;
        return this._m_patLed;
      }
    });
    Object.defineProperty(TechStatusPayload.prototype, 'redLed', {
      get: function() {
        if (this._m_redLed !== undefined)
          return this._m_redLed;
        this._m_redLed = this.redLedB1 << 1 | this.redLedB0;
        return this._m_redLed;
      }
    });
    Object.defineProperty(TechStatusPayload.prototype, 'vddVoltage', {
      get: function() {
        if (this._m_vddVoltage !== undefined)
          return this._m_vddVoltage;
        this._m_vddVoltage = this.vddB1 << 1 | this.vddB0;
        return this._m_vddVoltage;
      }
    });

    return TechStatusPayload;
  })();

  return WatchpatPacket;
})();
WatchpatPacket_.WatchpatPacket = WatchpatPacket;
});
