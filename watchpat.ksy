meta:
  id: watchpat_packet
  title: WatchPAT ONE BLE protocol packet
  endian: le

# Outer 24-byte header uses mixed endianness:
#   signature and opcode are big-endian (mirroring the original Java ByteBuffer
#   with Integer.reverseBytes / Short.reverseBytes before writing).
#   All other numeric fields are little-endian.
seq:
  - id: header
    type: packet_header
  - id: body
    size: header.total_len - 24
    type:
      switch-on: header.opcode
      cases:
        0x0200: session_confirm_payload   # SESSION_CONFIRM
        0x0500: session_confirm_payload   # CONFIG_RESPONSE (same layout)
        0x0800: data_packet_payload       # DATA_PACKET
        0x1300: bit_response_payload      # BIT_RESPONSE
        0x1600: tech_status_payload       # TECH_STATUS
        _: raw_payload

types:

  # -------------------------------------------------------------------------
  # Outer packet header (24 bytes, mixed endian)
  # -------------------------------------------------------------------------
  packet_header:
    seq:
      - id: signature
        contents: [0xBB, 0xBB]
      - id: opcode
        type: u2be
      - id: timestamp
        type: u8
      - id: packet_id
        type: u4
      - id: total_len
        type: u2
      - id: opcode_dependent
        type: u2
      - id: reserved
        type: u2
      - id: crc
        type: u2

  # -------------------------------------------------------------------------
  # SESSION_CONFIRM / CONFIG_RESPONSE payload
  # Fields are at fixed byte offsets; use positional instances so shorter
  # payloads (e.g. from older firmware) simply return null for the absent fields.
  # -------------------------------------------------------------------------
  session_confirm_payload:
    instances:
      hw_major:
        pos: 0
        type: u1
      hw_minor:
        pos: 1
        type: u1
      fw_version:
        pos: 2
        type: u2
      serial_number:
        pos: 54
        type: u4
        if: _io.size >= 58
      pin_code_raw:
        pos: 221
        type: u2
        if: _io.size >= 223
      device_subtype:
        pos: 235
        type: u1
        if: _io.size >= 236
      is_wcp_less:
        value: (device_subtype & 0x55) != 0
        if: _io.size >= 236
      is_wp1m:
        value: (device_subtype & 0x02) != 0
        if: _io.size >= 236
      has_finger_detection:
        value: (device_subtype & 0x08) != 0
        if: _io.size >= 236

  # -------------------------------------------------------------------------
  # BIT_RESPONSE payload (4-byte LE flags word)
  # -------------------------------------------------------------------------
  bit_response_payload:
    seq:
      - id: raw_flags
        type: u4
    instances:
      battery_depleted:
        value: (raw_flags & 0x001) != 0
      battery_low:
        value: (raw_flags & 0x002) != 0
      actigraph_error:
        value: (raw_flags & 0x004) != 0
      naf_error:
        value: (raw_flags & 0x008) != 0
      vdd_error:
        value: (raw_flags & 0x010) != 0
      used_device:
        value: (raw_flags & 0x020) != 0
      flash_error:
        value: (raw_flags & 0x040) != 0
      probe_led_error:
        value: (raw_flags & 0x080) != 0
      probe_photo_error:
        value: (raw_flags & 0x100) != 0
      probe_failure:
        value: (raw_flags & 0x200) != 0
      spb_error:
        value: (raw_flags & 0x400) != 0

  # -------------------------------------------------------------------------
  # TECH_STATUS payload (10 bytes)
  # Each sensor value is encoded as (high_byte << 1) | low_byte — NOT a
  # standard LE uint16.  The two raw bytes are read as seq fields; the actual
  # value is exposed through an instance.
  # -------------------------------------------------------------------------
  tech_status_payload:
    seq:
      - id: battery_b0
        type: u1
      - id: battery_b1
        type: u1
      - id: vdd_b0
        type: u1
      - id: vdd_b1
        type: u1
      - id: ir_led_b0
        type: u1
      - id: ir_led_b1
        type: u1
      - id: red_led_b0
        type: u1
      - id: red_led_b1
        type: u1
      - id: pat_led_b0
        type: u1
      - id: pat_led_b1
        type: u1
    instances:
      battery_voltage:
        value: (battery_b1 << 1) | battery_b0
      vdd_voltage:
        value: (vdd_b1 << 1) | vdd_b0
      ir_led:
        value: (ir_led_b1 << 1) | ir_led_b0
      red_led:
        value: (red_led_b1 << 1) | red_led_b0
      pat_led:
        value: pat_led_b0 | (pat_led_b1 << 1)

  # -------------------------------------------------------------------------
  # DATA_PACKET payload — stream of contiguous logical records
  # Records are assumed contiguous (no gap bytes between them).  Any trailing
  # bytes that don't form a complete record will cause a parse error; the
  # wrapper should catch ValidationNotEqualError to handle truncated packets.
  # -------------------------------------------------------------------------
  data_packet_payload:
    seq:
      - id: record_count
        type: u1
      - id: sub_header
        type: u2
      - id: records
        type: logical_record
        repeat: eos

  # -------------------------------------------------------------------------
  # One logical record (12-byte header + variable payload)
  # -------------------------------------------------------------------------
  logical_record:
    seq:
      - id: sync
        contents: [0xAA, 0xAA]
      - id: record_id
        type: u1
      - id: record_type
        type: u1
      - id: payload_len
        type: u2
      - id: rate
        type: u2
      - id: flags
        type: u4
      - id: payload
        size: payload_len
        type:
          switch-on: (record_id << 8) | record_type
          cases:
            0x0510: metric_payload      # METRIC_05_10
            0x0600: motion_payload      # MOTION_06_00
            _: raw_payload
            # 0x0111 OxiA, 0x0211 OxiB, 0x0311 PAT  → byte-delta waveform
            # 0x0401 Chest                            → nibble-delta waveform
            # 0x0C00 EVENT, 0x0D00 EVENT payload      → raw event bytes
            # Waveform and event decoding is stateful and handled in wrapper code.

  # -------------------------------------------------------------------------
  # METRIC_05_10 record payload — single once-per-second signed integer
  # -------------------------------------------------------------------------
  metric_payload:
    seq:
      - id: value
        type: s4

  # -------------------------------------------------------------------------
  # MOTION_06_00 record payload — five 16-byte subframes
  # -------------------------------------------------------------------------
  motion_payload:
    seq:
      - id: subframes
        type: motion_subframe
        repeat: eos

  # 16-byte motion subframe: marker + sensors + CRC
  motion_subframe:
    seq:
      - id: marker
        contents: [0xDD, 0xDD, 0xA3, 0x57]   # 0x57A3DDDD little-endian
      - id: field_a
        type: u2
      - id: field_b
        type: u2
      - id: x
        type: s2
      - id: y
        type: s2
      - id: z
        type: s2
      - id: crc
        type: u2

  # -------------------------------------------------------------------------
  # Catch-all — used for unknown opcodes and waveform/event record payloads
  # -------------------------------------------------------------------------
  raw_payload:
    seq:
      - id: data
        size-eos: true
