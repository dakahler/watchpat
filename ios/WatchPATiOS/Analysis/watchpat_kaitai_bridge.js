function watchpatHexToBytes(hex) {
  var out = new Uint8Array(hex.length / 2);
  for (var i = 0; i < hex.length; i += 2) {
    out[i / 2] = parseInt(hex.substr(i, 2), 16);
  }
  return out;
}

function watchpatBytesToBase64(bytes) {
  var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  var out = "";
  for (var i = 0; i < bytes.length; i += 3) {
    var a = bytes[i];
    var b = i + 1 < bytes.length ? bytes[i + 1] : 0;
    var c = i + 2 < bytes.length ? bytes[i + 2] : 0;
    var triple = (a << 16) | (b << 8) | c;
    out += chars[(triple >> 18) & 63];
    out += chars[(triple >> 12) & 63];
    out += i + 1 < bytes.length ? chars[(triple >> 6) & 63] : "=";
    out += i + 2 < bytes.length ? chars[triple & 63] : "=";
  }
  return out;
}

function watchpatNormalizeRecord(record) {
  var out = {
    record_id: record.recordId,
    record_type: record.recordType,
    payload_len: record.payloadLen,
    rate: record.rate,
    flags: record.flags,
    raw_payload_b64: watchpatBytesToBase64(record._raw_payload || new Uint8Array([]))
  };
  var kind = (record.recordId << 8) | record.recordType;
  if (kind === 0x0510 && record.payload) {
    out.metric_value = record.payload.value;
  }
  if (kind === 0x0600 && record.payload) {
    out.motion_subframes = [];
    for (var i = 0; i < record.payload.subframes.length; i++) {
      var sf = record.payload.subframes[i];
      out.motion_subframes.push({
        field_a: sf.fieldA,
        field_b: sf.fieldB,
        x: sf.x,
        y: sf.y,
        z: sf.z,
        crc: sf.crc
      });
    }
  }
  return out;
}

function parseWatchPatPacketHex(hex) {
  var bytes = watchpatHexToBytes(hex);
  var stream = new KaitaiStream.KaitaiStream(bytes);
  var parsed = new WatchpatPacket.WatchpatPacket(stream);
  var out = {
    header: {
      opcode: parsed.header.opcode,
      packet_id: parsed.header.packetId,
      total_len: parsed.header.totalLen
    }
  };
  if (parsed.body && parsed.body.records) {
    out.records = [];
    for (var i = 0; i < parsed.body.records.length; i++) {
      out.records.push(watchpatNormalizeRecord(parsed.body.records[i]));
    }
  } else {
    out.records = [];
  }
  return JSON.stringify(out);
}
