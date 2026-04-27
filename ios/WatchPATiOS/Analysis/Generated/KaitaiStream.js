(function (root, factory) {
  if (typeof define === "function" && define.amd) {
    define(["exports"], factory);
  } else if (typeof exports === "object" && exports !== null && typeof exports.nodeType !== "number") {
    factory(exports);
  } else {
    factory(root.KaitaiStream || (root.KaitaiStream = {}));
  }
})(typeof self !== "undefined" ? self : this, function (KaitaiStream_) {
  function ValidationNotEqualError(expected, actual, io, srcPath) {
    this.name = "ValidationNotEqualError";
    this.expected = expected;
    this.actual = actual;
    this.io = io;
    this.srcPath = srcPath;
    this.message = "Expected " + expected + " but got " + actual;
  }
  ValidationNotEqualError.prototype = Object.create(Error.prototype);
  ValidationNotEqualError.prototype.constructor = ValidationNotEqualError;

  function KaitaiStream(data) {
    if (data instanceof Uint8Array) {
      this._buffer = data;
    } else if (data instanceof ArrayBuffer) {
      this._buffer = new Uint8Array(data);
    } else {
      this._buffer = new Uint8Array(0);
    }
    this.pos = 0;
    this.size = this._buffer.length;
  }

  KaitaiStream.prototype.isEof = function () {
    return this.pos >= this.size;
  };

  KaitaiStream.prototype.seek = function (newPos) {
    this.pos = newPos;
  };

  KaitaiStream.prototype.readU1 = function () {
    return this._buffer[this.pos++];
  };

  KaitaiStream.prototype.readBytes = function (count) {
    var end = this.pos + count;
    var out = this._buffer.slice(this.pos, end);
    this.pos = end;
    return out;
  };

  KaitaiStream.prototype.readBytesFull = function () {
    return this.readBytes(this.size - this.pos);
  };

  KaitaiStream.prototype.readU2le = function () {
    var a = this.readU1();
    var b = this.readU1();
    return a | (b << 8);
  };

  KaitaiStream.prototype.readU2be = function () {
    var a = this.readU1();
    var b = this.readU1();
    return (a << 8) | b;
  };

  KaitaiStream.prototype.readU4le = function () {
    var a = this.readU1();
    var b = this.readU1();
    var c = this.readU1();
    var d = this.readU1();
    return (a >>> 0) + ((b << 8) >>> 0) + ((c << 16) >>> 0) + ((d << 24) >>> 0);
  };

  KaitaiStream.prototype.readS2le = function () {
    var value = this.readU2le();
    return value >= 0x8000 ? value - 0x10000 : value;
  };

  KaitaiStream.prototype.readS4le = function () {
    var value = this.readU4le();
    return value >= 0x80000000 ? value - 0x100000000 : value;
  };

  KaitaiStream.prototype.readU8le = function () {
    var low = this.readU4le();
    var high = this.readU4le();
    return low + high * 4294967296;
  };

  KaitaiStream.byteArrayCompare = function (a, b) {
    if (a.length !== b.length) {
      return a.length < b.length ? -1 : 1;
    }
    for (var i = 0; i < a.length; i++) {
      if (a[i] !== b[i]) {
        return a[i] < b[i] ? -1 : 1;
      }
    }
    return 0;
  };

  KaitaiStream.ValidationNotEqualError = ValidationNotEqualError;
  KaitaiStream_.KaitaiStream = KaitaiStream;
  KaitaiStream_.ValidationNotEqualError = ValidationNotEqualError;
});
