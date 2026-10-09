# The DATA payload, as the device firmware builds it

These notes describe the `DATA_PACKET` payload from the other side of the
link: the code in the WatchPAT ONE that assembles it. They come from a
decompile of the device firmware (WP1 4.2.1210, the nRF52 application) and
from recordings off one unit running 4.2.1122. Everything below was then
run against this repository's own full-night capture, `tests/testdata2.dat`
(21,939 packets), and the results are given where they apply.

They are notes, not a change to the code. Where they disagree with
`watchpat.ksy`, `watchpat_protocol.py` or the README, the difference is
listed under [What this changes](#what-this-changes) so it can be checked
and taken or left.

Each claim is marked with where it comes from:

- **[fw]** read from the firmware
- **[file]** checked against `tests/testdata2.dat`
- **[unit]** seen on one other unit; may not hold for yours

## The packet

One DATA packet is one second of recording **[fw, file]**. The device
closes a packet when channel 1 holds 100 samples, writes it to flash, and
streams it from flash later, one packet per ACK.

- **Header timestamp** (offset 4, u64): centiseconds on the device clock,
  and the tick of the packet's *last* sample. Consecutive packets differ by
  exactly 100 **[file]**. The clock is whatever the session start set it
  to; in `testdata2.dat` it was never set, so it counts from power-up
  (first packet 16400).
- **Payload**: a u8 frame count, a u16 that is always 1, then that many
  frames back to back with nothing between them.

`tests/testdata.dat` and `tests/testdata2.dat` hold whole packets, 24-byte
header included, behind each length prefix. The README's "File Format"
section says the body only.

## The frame header

Twelve bytes, then `length` bytes of data **[fw, file]**:

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | `AA AA` |
| 2 | 1 | channel id |
| 3 | 1 | type: low nibble 1 = delta-compressed; high nibble 1 on the optical channels |
| 4 | 2 | data length in bytes |
| 6 | 2 | **sample count** (records, for channels 6 and 13) |
| 8 | 2 | acquisition settings, optical channels only; see [below](#the-settings-word) |
| 10 | 2 | flags: bit 15 = the compressed stream overflowed and its samples were dropped |

Bytes 6-7 are a count, not a rate: it is 100 on the 100 Hz channels, 5 on
the chest records, 1 on the once-a-second and once-a-minute channels, and
it drops in the short last packet of a recording. Bytes 8-11 are two u16
fields, not one u32.

## Channels

Frames come in this fixed order, and an empty channel is left out
**[fw, file]**. In `testdata2.dat` the order is `1, 2, 3, 5, 6, 4` in 21,572
packets, with channel 12 between 5 and 6 in 365 and channel 13 last in 2.

| Id | Type | Content | Per packet |
|---|---|---|---|
| 1 | `0x11` | **red** light, u16, compressed | 100 |
| 2 | `0x11` | **infrared** light, u16, compressed | 100 |
| 3 | `0x11` | **PAT** light, u16, compressed | 100 |
| 5 | `0x10` | **ambient** light (no LED lit), one signed i32 | 1 |
| 12 (`0x0C`) | `0x00` | **battery**, u16 millivolts | 1, once a minute |
| 6 | `0x00` | chest-sensor records, 16 bytes each | 5 |
| 4 | `0x01` | **wrist accelerometer**, one axis, signed, compressed | 100 |
| 13 (`0x0D`) | `0x00` | events, 10 bytes each: u16 code, u64 argument | rare |

The firmware also defines channel 14 (a second kind of chest-sensor record,
compressed) and channel 10 (channel 6's slot when no digital chest sensor
is detected). Neither appears in `testdata2.dat` or on the other unit.

**The optical front end is a TI AFE4404** **[fw]**. It pulses three LEDs in
turn and reads a fourth phase with nothing lit. Channels 1-3 are each
`(reading - ambient)`, shifted right by 5 (by 6 for PAT), clamped to
0..`0xFFFE`. Which channel is which colour is fixed in the firmware, three
ways: the `RED_`/`IR_`/`PAT_` cable thresholds load into check slots 1, 2
and 3; the self-test logs "AFE red / ir / pat led test" for those slots;
and the PAT signal-adjustment limits drive a different LED's current from
the red and infrared ones. So **channel 1 is red and channel 2 is
infrared**, and nothing needs to be inferred from the data.

**No channel holds a computed value** **[fw]**. The device records light,
motion and the chest sensor's records; SpO2, pulse rate and PAT amplitude
are all computed off the device.

**Channel 5 is signed.** It is the AFE's two's-complement ambient reading.
In `testdata2.dat` it runs from -449 to -2325 **[file]**.

**Channel 12 is the battery**: the mean voltage since the last reading, in
millivolts. In `testdata2.dat` there is one every 60 packets, falling from
1607 to 1428 over the night **[file]**.

**Channel 4 is on the wrist, not the chest** **[fw]**. It is one axis of the
wrist unit's accelerometer at 100 Hz, 12-bit signed. On the other unit the
axis runs along the forearm, about 1000 counts per g **[unit]**. The
firmware picks the axis from a parameter (`ACTAxisToUse`, default Y).

### Events (channel 13)

| Code | Argument | Meaning |
|---|---|---|
| `0xF010` | timestamp of the first sample | recording started |
| `0xF030` | timestamp | recording ended; also follows every stop code |
| `0xF008` | timestamp | battery too low: stop |
| `0xF064` | timestamp | probe cable fault: stop |
| `0xF065`, `0xF040` | timestamp | a packet could not be written to flash: stop |
| `0xF005`, `0xF006` | bytes of the chest sensor's identity record | chest sensor identity, parts 1 and 2 |

`testdata2.dat` has `0xF010` in its first packet and `0xF005` + `0xF006`
together in packet 443 (counting from 0) **[file]**.

## Delta compression

Used when the type's low nibble is 1: channels 1, 2, 3 and 4 **[fw, file]**.
The data is a run of little-endian **u16 words**, not bytes.

| | Channels 1, 2, 3 | Channel 4 |
|---|---|---|
| delta field | 7 bits | 5 bits |
| deltas per word | 2 | 3 |
| largest delta | ±63 | ±15 |
| "slot unused" marker | `0x40` | `0x10` |
| after decoding | nothing | subtract `0x800` |

1. Word 0 is the first sample, as is.
2. A word with **bit 15 set** is an absolute sample: the low 15 bits are a
   two's-complement offset from the **first** sample (not the previous).
3. A word with **bit 15 clear** packs deltas from the previous sample,
   lowest bits first: `d1 | d2 << 7`, or `d1 | d2 << 5 | d3 << 10`. A slot
   holding the marker ends the word.
4. Stop at the header's sample count. The last word of a frame can be
   part-filled without a marker, so the count is what ends the frame.

The encoder always writes the second sample as an absolute word, and after
that any sample whose step is too big to pack. So a quiet second is 102
bytes on an optical channel (first, absolute, 49 words of two) and 70 on
the accelerometer (first, absolute, 33 words of three), which are the
commonest lengths in the file.

If a sample gets `0x3FFF` or more from the first, the frame gives up:
length becomes 2, flags bit 15 is set, and the count still says 100. Those
samples are gone. It does not happen in `testdata2.dat`.

A reference decoder:

```python
import struct

def undelta(data: bytes, count: int, bits: int) -> list[int]:
    """bits = 7 for channels 1-3, 5 for channel 4 (then subtract 0x800)."""
    words = struct.unpack(f"<{len(data) // 2}H", data[: len(data) // 2 * 2])
    if not words:
        return []
    marker, mask = 1 << (bits - 1), (1 << bits) - 1
    first = prev = words[0]
    out = [first]
    for w in words[1:]:
        if len(out) >= count:
            break
        if w & 0x8000:
            off = w & 0x7FFF
            prev = first + (off - 0x8000 if off & 0x4000 else off)
            out.append(prev)
            continue
        for slot in range(15 // bits):
            d = (w >> (slot * bits)) & mask
            if d == marker or len(out) >= count:
                break
            prev += d - (mask + 1) if d & marker else d
            out.append(prev)
    return out
```

Run over `testdata2.dat` **[file]**:

- all 87,756 compressed frames decode to exactly the sample count in their
  header (100 each);
- the step from the last sample of one packet to the first of the next is
  the same size as the steps inside a packet, on all four channels (median
  10-18 counts on the optical channels, 2 on the accelerometer), so the
  values join up across packets as well as the counts.

The byte-wise zigzag decoder in `watchpat_protocol.py` returns one sample
per byte after the seed, so 101 samples for a 102-byte frame whose header
says 100, and more for longer frames. It does not return the header's count
for any of the 65,817 optical frames in the file.

## The settings word

Bytes 8-9 of an optical channel's frame header record the AFE's state for
that second **[fw]**:

| Bits | Field |
|---|---|
| 0-5 | LED current code, 0-63 |
| 7-9 | a gain setting shared by all channels |
| 10-13 | this phase's offset-DAC current |
| 14 | this phase's offset-DAC polarity |

The firmware adjusts these during a recording, and each change is a step
in the light levels. `testdata2.dat` has three **[file]**:

| Packet | Change | Red | Infrared | PAT |
|---|---|---|---|---|
| 29 | gain 4 → 3; PAT current 18 → 8 | ×1.88 | ×1.94 | ×0.79 |
| 1499 | gain 3 → 2; infrared current 30 → 15 | ×1.97 | ×0.98 | ×1.98 |
| 5099 | infrared current 15 → 30 | ×1.00 | ×2.04 | ×1.02 |

Two things follow from that table:

- **Each step down in the gain field doubles the level.**
- **Channel 1's current field is a copy of channel 2's.** At packet 5099
  the field changes on both channels and only the infrared level moves. The
  firmware has what looks like a copy-paste slip here, so red's own LED
  current is not recorded anywhere in the stream.

A ratio-of-ratios SpO2 survives a level change in principle, since each
channel's pulse is taken as a share of its own level, but a window that
spans the change does not. Those seconds can be found from this word.

## The chest record (channel 6)

Five 16-byte records a second. The chest sensor is a separate device on a
cable; the main board checks each record's CRC and copies it through
without looking inside **[fw]**, so the field meanings are from recordings.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | `DD DD A3 57` (two sync words) |
| 4 | 2 | f1, i16: sound or vibration level |
| 6 | 2 | f2, i16: sound or vibration level |
| 8 | 2 | x, i16: acceleration, about 1 mg per count |
| 10 | 2 | y, i16 |
| 12 | 2 | z, i16 |
| 14 | 2 | CRC-16/CCITT-FALSE of bytes 0-13, little-endian |

- **A record can arrive with its first sync word zeroed** (`00 00 A3 57`),
  the rest intact and the CRC correct once `DD DD` is put back. One of the
  109,695 records in `testdata2.dat` is like this, and no record fails its
  CRC otherwise **[file]**.
- **f1 and f2 are both sound levels**, never negative: about 24 in a quiet
  room, 110-140 humming, and pinned near 1010 when the sensor is tapped
  **[unit]**. The file agrees on the floor and the ceiling: median 24,
  maximum 1011 and 1010 **[file]**. What separates the two is not known.
- **Posture on the other unit** **[unit]**:

  | Posture | x | y | z |
  |---|---|---|---|
  | sitting upright | 240 | **-809** | 565 |
  | lying on the back | 78 | 465 | **+1076** |
  | lying on the left side | **+1096** | -77 | 280 |
  | lying on the right side | **-821** | -86 | 445 |

  That is x for left/right and y (negative) for upright, where the README
  has y for left/right and x for upright. Both agree on z for supine. In
  `testdata2.dat` x spans -895 to +1159 while y is below -279 in under
  1 % of records, which fits x being the side-to-side axis for a night
  spent lying down. One unit is not enough to settle how the sensor sits
  in every case.

## What this changes

Against the README's "Sensor Channels" table and the decoders:

| Now | From the firmware |
|---|---|
| `01/11`, `02/11`: "Oximetry A / B", colour inferred | channel 1 red, channel 2 infrared |
| `01/11`-`03/11` codec: 2-byte seed + zigzag8 deltas | u16 words: first sample, absolute words, two 7-bit deltas per word |
| `04/01`: chest / respiratory effort, nibble deltas | wrist accelerometer, one axis; three 5-bit deltas per word, minus `0x800` |
| `05/10`: derived metric | ambient light, signed |
| `0C/00`: event code | battery, millivolts, once a minute |
| `0D/00`: event payload | events: u16 code + u64 argument |
| frame header bytes 6-7: sample rate | sample count |
| frame header bytes 8-11: flags (u32) | settings (u16), then flags (u16) |

The one with consequences downstream is `04/01`: the respiratory-effort
features and the parts of sleep staging that use them are reading wrist
movement.

## What is still not known

- The SpO2 calibration for this probe. The firmware has none; the curve
  lives in the vendor's analysis software.
- What separates the chest record's two sound fields, and their units.
- The scale of the chest accelerometer across units: the vector's length
  has a median of about 1220 in `testdata2.dat` and 940-1020 on the other
  unit.
- Which firmware version `testdata2.dat` was recorded on. The 4.2.1122 unit
  refuses one command that 4.2.1210 accepts, so versions do differ.
