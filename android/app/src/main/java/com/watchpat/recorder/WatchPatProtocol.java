package com.watchpat.recorder;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * WatchPAT ONE BLE protocol implementation.
 *
 * Implements the same binary packet format used by the WatchPAT Android app v4.2.0,
 * reverse-engineered in watchpat_ble.py. Packets use a 24-byte header over the
 * Nordic UART Service (NUS), chunked into 20-byte BLE writes.
 *
 * Packet header layout (24 bytes):
 *   offset  size  field
 *   0       2     Signature (0xBBBB, big-endian)
 *   2       2     Opcode (big-endian)
 *   4       8     Timestamp (little-endian uint64, unused — always 0)
 *   12      4     Packet ID (little-endian uint32)
 *   16      2     Total length incl. header (little-endian uint16)
 *   18      2     Opcode-dependent (little-endian uint16)
 *   20      2     Reserved
 *   22      2     CRC-16 CCITT (little-endian uint16, computed over full packet with CRC=0)
 *   24+     var   Payload
 */
public class WatchPatProtocol {

    // NUS service and characteristic UUIDs
    public static final String NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
    public static final String NUS_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"; // write
    public static final String NUS_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"; // notify
    public static final String CCCD_UUID        = "00002902-0000-1000-8000-00805f9b34fb";

    public static final int HEADER_SIZE    = 24;
    public static final int MAX_BLE_CHUNK  = 20;

    // Command opcodes
    public static final int OPCODE_ACK               = 0x0000;
    public static final int OPCODE_SESSION_START      = 0x0100;
    public static final int OPCODE_START_ACQUISITION  = 0x0600;
    public static final int OPCODE_STOP_ACQUISITION   = 0x0700;

    // Response opcodes
    public static final int RESP_ACK             = 0x0000;
    public static final int RESP_SESSION_CONFIRM = 0x0200;
    public static final int RESP_DATA_PACKET     = 0x0800;
    public static final int RESP_END_OF_TEST     = 0x0900;
    public static final int RESP_ERROR_STATUS    = 0x0A00;

    private static int packetCounter = 0;

    // -----------------------------------------------------------------------
    // CRC-16 CCITT (polynomial 0x1021, init 0xFFFF, bit-by-bit)
    // -----------------------------------------------------------------------
    public static int crc16(byte[] data) {
        int crc = 0xFFFF;
        for (byte b : data) {
            int mask = 0x80;
            while (mask > 0) {
                boolean carry = (crc & 0x8000) != 0;
                if ((b & mask) != 0) carry = !carry;
                crc = (crc << 1) & 0xFFFF;
                if (carry) crc ^= 0x1021;
                mask >>= 1;
            }
        }
        return crc & 0xFFFF;
    }

    // -----------------------------------------------------------------------
    // Packet building
    // -----------------------------------------------------------------------

    private static byte[] buildHeader(int opcode, int packetId, int totalLen, int opcodeDep) {
        byte[] hdr = new byte[HEADER_SIZE];
        // Signature: big-endian 0xBBBB
        hdr[0] = (byte) 0xBB;
        hdr[1] = (byte) 0xBB;
        // Opcode: big-endian
        hdr[2] = (byte) ((opcode >> 8) & 0xFF);
        hdr[3] = (byte) (opcode & 0xFF);
        // Timestamp: little-endian uint64, bytes 4-11 (always 0)
        // Packet ID: little-endian uint32
        hdr[12] = (byte) (packetId & 0xFF);
        hdr[13] = (byte) ((packetId >> 8) & 0xFF);
        hdr[14] = (byte) ((packetId >> 16) & 0xFF);
        hdr[15] = (byte) ((packetId >> 24) & 0xFF);
        // Total length: little-endian uint16
        hdr[16] = (byte) (totalLen & 0xFF);
        hdr[17] = (byte) ((totalLen >> 8) & 0xFF);
        // Opcode-dependent: little-endian uint16
        hdr[18] = (byte) (opcodeDep & 0xFF);
        hdr[19] = (byte) ((opcodeDep >> 8) & 0xFF);
        // Reserved (bytes 20-21) and CRC placeholder (bytes 22-23) remain 0
        return hdr;
    }

    /** Compute CRC over the full packet (with CRC field zeroed) and write it at offset 22. */
    private static void finalizePacket(byte[] packet) {
        packet[22] = 0;
        packet[23] = 0;
        int crc = crc16(packet);
        packet[22] = (byte) (crc & 0xFF);
        packet[23] = (byte) ((crc >> 8) & 0xFF);
    }

    private static List<byte[]> chunkPacket(byte[] packet) {
        List<byte[]> chunks = new ArrayList<>();
        for (int i = 0; i < packet.length; i += MAX_BLE_CHUNK) {
            int end = Math.min(i + MAX_BLE_CHUNK, packet.length);
            byte[] chunk = new byte[end - i];
            System.arraycopy(packet, i, chunk, 0, chunk.length);
            chunks.add(chunk);
        }
        return chunks;
    }

    private static List<byte[]> buildCommand(int opcode, byte[] payload) {
        int pid = ++packetCounter;
        int payloadLen = (payload != null) ? payload.length : 0;
        int totalLen = HEADER_SIZE + payloadLen;
        byte[] hdr = buildHeader(opcode, pid, totalLen, 0);
        byte[] packet = new byte[totalLen];
        System.arraycopy(hdr, 0, packet, 0, HEADER_SIZE);
        if (payload != null && payloadLen > 0) {
            System.arraycopy(payload, 0, packet, HEADER_SIZE, payloadLen);
        }
        finalizePacket(packet);
        return chunkPacket(packet);
    }

    /**
     * SESSION_START payload (20 bytes):
     *   4 bytes  mobile_id (big-endian uint32) = 0
     *   1 byte   mode (1 = normal, 4 = resume)
     *  14 bytes  os_version string padded with nulls ("14\0\0...\0")
     *   1 byte   null terminator
     */
    public static List<byte[]> buildSessionStart(int mode) {
        byte[] payload = new byte[20];
        // mobile_id = 0 (already zeroed)
        payload[4] = (byte) mode;
        // os_version = "14" at bytes 5-18, rest zero
        byte[] osVer = "14".getBytes(StandardCharsets.US_ASCII);
        System.arraycopy(osVer, 0, payload, 5, Math.min(osVer.length, 14));
        // null terminator at byte 19 (already 0)
        return buildCommand(OPCODE_SESSION_START, payload);
    }

    public static List<byte[]> buildSessionStart() {
        return buildSessionStart(1); // mode 1 = normal
    }

    public static List<byte[]> buildStartAcquisition() {
        return buildCommand(OPCODE_START_ACQUISITION, null);
    }

    public static List<byte[]> buildStopAcquisition() {
        return buildCommand(OPCODE_STOP_ACQUISITION, null);
    }

    /**
     * ACK payload (5 bytes):
     *   2 bytes  response opcode (big-endian)
     *   1 byte   status (0 = OK)
     *   2 bytes  padding (0x00 0x00)
     */
    public static List<byte[]> buildAck(int responseOpcode, int status, int responseId) {
        byte[] payload = new byte[5];
        payload[0] = (byte) ((responseOpcode >> 8) & 0xFF);
        payload[1] = (byte) (responseOpcode & 0xFF);
        payload[2] = (byte) status;
        // payload[3..4] = 0x00 0x00 (already zeroed)

        int totalLen = HEADER_SIZE + payload.length;
        byte[] hdr = buildHeader(OPCODE_ACK, responseId, totalLen, 0);
        byte[] packet = new byte[totalLen];
        System.arraycopy(hdr, 0, packet, 0, HEADER_SIZE);
        System.arraycopy(payload, 0, packet, HEADER_SIZE, payload.length);
        finalizePacket(packet);
        return chunkPacket(packet);
    }

    // -----------------------------------------------------------------------
    // Packet field accessors
    // -----------------------------------------------------------------------

    public static int parseOpcode(byte[] packet) {
        if (packet.length < 4) return -1;
        return ((packet[2] & 0xFF) << 8) | (packet[3] & 0xFF);
    }

    public static int parsePacketId(byte[] packet) {
        if (packet.length < 16) return 0;
        return (packet[12] & 0xFF)
             | ((packet[13] & 0xFF) << 8)
             | ((packet[14] & 0xFF) << 16)
             | ((packet[15] & 0xFF) << 24);
    }

    /** Returns the payload bytes (everything after the 24-byte header). */
    public static byte[] extractPayload(byte[] packet) {
        if (packet.length <= HEADER_SIZE) return new byte[0];
        byte[] payload = new byte[packet.length - HEADER_SIZE];
        System.arraycopy(packet, HEADER_SIZE, payload, 0, payload.length);
        return payload;
    }

    // -----------------------------------------------------------------------
    // BLE packet reassembler
    // -----------------------------------------------------------------------

    /**
     * Reassembles complete WatchPAT packets from raw 20-byte BLE notification chunks.
     * Looks for the 0xBBBB signature, reads the total-length field, and buffers bytes
     * until a complete packet is available, then calls the callback.
     */
    public static class PacketReassembler {

        public interface PacketCallback {
            void onPacket(int opcode, int packetId, byte[] fullPacket);
        }

        private byte[] buf = new byte[8192];
        private int bufLen = 0;

        public void feed(byte[] chunk, PacketCallback callback) {
            // Append chunk to internal buffer
            if (bufLen + chunk.length > buf.length) {
                // Expand buffer
                byte[] bigger = new byte[Math.max(buf.length * 2, bufLen + chunk.length + 1024)];
                System.arraycopy(buf, 0, bigger, 0, bufLen);
                buf = bigger;
            }
            System.arraycopy(chunk, 0, buf, bufLen, chunk.length);
            bufLen += chunk.length;

            int pos = 0;
            while (pos < bufLen) {
                // Need at least 18 bytes to read length field (at offset 16)
                if (bufLen - pos < 18) break;

                // Check for 0xBBBB signature
                if ((buf[pos] & 0xFF) != 0xBB || (buf[pos + 1] & 0xFF) != 0xBB) {
                    pos++;
                    continue;
                }

                // Read total length (little-endian uint16 at offset 16)
                int totalLen = (buf[pos + 16] & 0xFF) | ((buf[pos + 17] & 0xFF) << 8);

                if (totalLen < HEADER_SIZE || totalLen > 4096) {
                    // Implausible length — skip this signature byte and resync
                    pos++;
                    continue;
                }

                if (bufLen - pos < totalLen) break; // need more data

                // Extract complete packet
                byte[] packet = new byte[totalLen];
                System.arraycopy(buf, pos, packet, 0, totalLen);

                int opcode   = ((buf[pos + 2] & 0xFF) << 8) | (buf[pos + 3] & 0xFF);
                int packetId = (buf[pos + 12] & 0xFF)
                             | ((buf[pos + 13] & 0xFF) << 8)
                             | ((buf[pos + 14] & 0xFF) << 16)
                             | ((buf[pos + 15] & 0xFF) << 24);

                callback.onPacket(opcode, packetId, packet);
                pos += totalLen;
            }

            // Compact buffer: move remaining bytes to the front
            if (pos > 0) {
                int remaining = bufLen - pos;
                if (remaining > 0) {
                    System.arraycopy(buf, pos, buf, 0, remaining);
                }
                bufLen = remaining;
            }
        }

        public void reset() {
            bufLen = 0;
        }
    }
}
