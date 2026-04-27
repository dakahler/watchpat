@preconcurrency import CoreBluetooth
import Foundation

enum WatchPatProtocol {
    static let headerSize = 24
    static let maxBLEChunk = 20

    nonisolated(unsafe) static let nusServiceUUID = CBUUID(string: "6e400001-b5a3-f393-e0a9-e50e24dcca9e")
    nonisolated(unsafe) static let nusRXCharUUID = CBUUID(string: "6e400002-b5a3-f393-e0a9-e50e24dcca9e")
    nonisolated(unsafe) static let nusTXCharUUID = CBUUID(string: "6e400003-b5a3-f393-e0a9-e50e24dcca9e")

    static let opcodeAck = 0x0000
    static let opcodeSessionStart = 0x0100
    static let opcodeStartAcquisition = 0x0600
    static let opcodeStopAcquisition = 0x0700

    static let respAck = 0x0000
    static let respSessionConfirm = 0x0200
    static let respDataPacket = 0x0800
    static let respEndOfTest = 0x0900
    static let respErrorStatus = 0x0A00

    nonisolated(unsafe) private static var packetCounter = UInt32(0)

    static func crc16(_ bytes: Data) -> UInt16 {
        var crc: UInt16 = 0xFFFF
        for byte in bytes {
            var mask: UInt8 = 0x80
            while mask > 0 {
                var carry = (crc & 0x8000) != 0
                if (byte & mask) != 0 {
                    carry = !carry
                }
                crc = (crc << 1) & 0xFFFF
                if carry {
                    crc ^= 0x1021
                }
                mask >>= 1
            }
        }
        return crc
    }

    static func nextPacketID() -> UInt32 {
        packetCounter &+= 1
        return packetCounter
    }

    static func chunkPacket(_ packet: Data) -> [Data] {
        var chunks: [Data] = []
        var offset = 0
        while offset < packet.count {
            let end = min(offset + maxBLEChunk, packet.count)
            chunks.append(packet.subdata(in: offset..<end))
            offset = end
        }
        return chunks
    }

    static func buildSessionStart(mode: UInt8 = 1) -> [Data] {
        var payload = Data(repeating: 0, count: 20)
        payload[4] = mode
        let osVersion = Array("17".utf8)
        payload.replaceSubrange(5..<(5 + osVersion.count), with: osVersion)
        return buildCommand(opcode: opcodeSessionStart, payload: payload)
    }

    static func buildStartAcquisition() -> [Data] {
        buildCommand(opcode: opcodeStartAcquisition, payload: Data())
    }

    static func buildStopAcquisition() -> [Data] {
        buildCommand(opcode: opcodeStopAcquisition, payload: Data())
    }

    static func buildAck(responseOpcode: Int, status: UInt8 = 0, responseID: UInt32) -> [Data] {
        var payload = Data()
        payload.append(UInt8((responseOpcode >> 8) & 0xFF))
        payload.append(UInt8(responseOpcode & 0xFF))
        payload.append(status)
        payload.append(0)
        payload.append(0)
        return buildPacket(opcode: opcodeAck, packetID: responseID, payload: payload)
    }

    private static func buildCommand(opcode: Int, payload: Data) -> [Data] {
        let packetID = nextPacketID()
        return buildPacket(opcode: opcode, packetID: packetID, payload: payload)
    }

    private static func buildPacket(opcode: Int, packetID: UInt32, payload: Data) -> [Data] {
        var packet = Data(repeating: 0, count: headerSize)
        packet[0] = 0xBB
        packet[1] = 0xBB
        packet[2] = UInt8((opcode >> 8) & 0xFF)
        packet[3] = UInt8(opcode & 0xFF)
        packet.writeLE(packetID, at: 12)
        packet.writeLE(UInt16(headerSize + payload.count), at: 16)
        packet.append(payload)
        let crc = crc16(packet)
        packet.writeLE(crc, at: 22)
        return chunkPacket(packet)
    }

    final class PacketReassembler {
        private var buffer = Data()

        func reset() {
            buffer.removeAll(keepingCapacity: true)
        }

        func feed(_ chunk: Data, onPacket: (Int, UInt32, Data) -> Void) {
            buffer.append(chunk)
            while buffer.count >= 18 {
                guard let signatureOffset = findSignature(in: buffer) else {
                    buffer.removeAll(keepingCapacity: true)
                    return
                }
                if signatureOffset > 0 {
                    buffer.removeFirst(signatureOffset)
                }
                guard buffer.count >= 18 else { return }
                let totalLength = Int(buffer.readUInt16LE(at: 16))
                guard totalLength >= WatchPatProtocol.headerSize, totalLength <= 4096 else {
                    buffer.removeFirst()
                    continue
                }
                guard buffer.count >= totalLength else { return }
                let packet = buffer.subdata(in: 0..<totalLength)
                let opcode = Int(packet.readUInt16BE(at: 2))
                let packetID = packet.readUInt32LE(at: 12)
                onPacket(opcode, packetID, packet)
                buffer.removeFirst(totalLength)
            }
        }

        private func findSignature(in data: Data) -> Int? {
            guard data.count >= 2 else { return nil }
            for index in 0..<(data.count - 1) where data[index] == 0xBB && data[index + 1] == 0xBB {
                return index
            }
            return nil
        }
    }
}

private extension Data {
    mutating func writeLE(_ value: UInt16, at offset: Int) {
        self[offset] = UInt8(value & 0xFF)
        self[offset + 1] = UInt8((value >> 8) & 0xFF)
    }

    mutating func writeLE(_ value: UInt32, at offset: Int) {
        self[offset] = UInt8(value & 0xFF)
        self[offset + 1] = UInt8((value >> 8) & 0xFF)
        self[offset + 2] = UInt8((value >> 16) & 0xFF)
        self[offset + 3] = UInt8((value >> 24) & 0xFF)
    }

    func readUInt16LE(at offset: Int) -> UInt16 {
        UInt16(self[offset]) | (UInt16(self[offset + 1]) << 8)
    }

    func readUInt16BE(at offset: Int) -> UInt16 {
        (UInt16(self[offset]) << 8) | UInt16(self[offset + 1])
    }

    func readUInt32LE(at offset: Int) -> UInt32 {
        UInt32(self[offset])
        | (UInt32(self[offset + 1]) << 8)
        | (UInt32(self[offset + 2]) << 16)
        | (UInt32(self[offset + 3]) << 24)
    }
}
