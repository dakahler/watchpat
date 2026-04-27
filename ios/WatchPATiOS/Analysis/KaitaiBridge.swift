import Foundation
import JavaScriptCore

struct ParsedWatchPatPacket: Decodable {
    struct Header: Decodable {
        let opcode: Int
        let packetID: UInt32
        let totalLen: Int

        private enum CodingKeys: String, CodingKey {
            case opcode
            case packetID = "packet_id"
            case totalLen = "total_len"
        }
    }

    struct Record: Decodable {
        struct MotionSubframe: Decodable {
            let fieldA: Int
            let fieldB: Int
            let x: Int
            let y: Int
            let z: Int
            let crc: Int

            private enum CodingKeys: String, CodingKey {
                case fieldA = "field_a"
                case fieldB = "field_b"
                case x, y, z, crc
            }
        }

        let recordID: Int
        let recordType: Int
        let payloadLen: Int
        let rate: Int
        let flags: Int
        let rawPayloadB64: String
        let metricValue: Int?
        let motionSubframes: [MotionSubframe]?

        private enum CodingKeys: String, CodingKey {
            case recordID = "record_id"
            case recordType = "record_type"
            case payloadLen = "payload_len"
            case rate
            case flags
            case rawPayloadB64 = "raw_payload_b64"
            case metricValue = "metric_value"
            case motionSubframes = "motion_subframes"
        }
    }

    let header: Header
    let records: [Record]
}

enum KaitaiBridgeError: Error {
    case resourceMissing(String)
    case parserSetupFailed
    case parseFailed(String)
}

final class KaitaiBridge {
    private let context: JSContext
    private let parseFunction: JSValue

    init(bundle: Bundle = .main) throws {
        guard let context = JSContext() else {
            throw KaitaiBridgeError.parserSetupFailed
        }
        context.exceptionHandler = { _, exception in
            if let exception {
                print("JS exception: \(exception)")
            }
        }
        self.context = context

        try Self.loadScript(named: "KaitaiStream", bundle: bundle, into: context)
        try Self.loadScript(named: "WatchpatPacket", bundle: bundle, into: context)
        try Self.loadScript(named: "watchpat_kaitai_bridge", bundle: bundle, into: context)
        guard let parseFunction = context.objectForKeyedSubscript("parseWatchPatPacketHex") else {
            throw KaitaiBridgeError.parserSetupFailed
        }
        self.parseFunction = parseFunction
    }

    func parsePacket(_ packetData: Data) throws -> ParsedWatchPatPacket {
        let hex = packetData.map { String(format: "%02x", $0) }.joined()
        guard let jsonString = parseFunction.call(withArguments: [hex])?.toString() else {
            throw KaitaiBridgeError.parseFailed("parser returned no JSON")
        }
        guard let data = jsonString.data(using: .utf8) else {
            throw KaitaiBridgeError.parseFailed("invalid JSON encoding")
        }
        do {
            return try JSONDecoder().decode(ParsedWatchPatPacket.self, from: data)
        } catch {
            throw KaitaiBridgeError.parseFailed(error.localizedDescription)
        }
    }

    private static func loadScript(named name: String, bundle: Bundle, into context: JSContext) throws {
        guard let url = bundle.url(forResource: name, withExtension: "js") else {
            throw KaitaiBridgeError.resourceMissing(name)
        }
        let source = try String(contentsOf: url)
        context.evaluateScript(source)
    }
}
