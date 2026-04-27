import Foundation
import Network

struct MQTTSettings: Equatable {
    var serverURI: String = ""
    var username: String = ""
    var password: String = ""

    static let prefsKey = "watchpat_ios_mqtt"

    static func load() -> MQTTSettings {
        guard
            let data = UserDefaults.standard.data(forKey: prefsKey),
            let decoded = try? JSONDecoder().decode(MQTTSettings.self, from: data)
        else {
            return MQTTSettings()
        }
        return decoded
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) {
            UserDefaults.standard.set(data, forKey: MQTTSettings.prefsKey)
        }
    }

    var normalizedHostPort: (host: String, port: UInt16)? {
        let trimmed = serverURI.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let value = trimmed.contains("://") ? trimmed : "tcp://\(trimmed)"
        guard let components = URLComponents(string: value), let host = components.host else {
            return nil
        }
        let port = UInt16(components.port ?? 1883)
        return (host, port)
    }
}

extension MQTTSettings: Codable {}

final class MQTTClient {
    private let settings: MQTTSettings
    private let clientID: String
    private var connection: NWConnection?

    init(settings: MQTTSettings, clientID: String) {
        self.settings = settings
        self.clientID = clientID
    }

    func connect() async throws {
        guard let target = settings.normalizedHostPort else {
            throw NSError(domain: "MQTTClient", code: 1, userInfo: [NSLocalizedDescriptionKey: "MQTT server URI is empty"])
        }
        let connection = NWConnection(
            host: NWEndpoint.Host(target.host),
            port: NWEndpoint.Port(rawValue: target.port)!,
            using: .tcp
        )
        self.connection = connection
        try await withCheckedThrowingContinuation { continuation in
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    continuation.resume()
                case .failed(let error):
                    continuation.resume(throwing: error)
                default:
                    break
                }
            }
            connection.start(queue: .global(qos: .userInitiated))
        }

        try await send(buildConnectPacket())
        let connack = try await receive(minimum: 4)
        guard connack.count >= 4, connack[0] == 0x20, connack[3] == 0x00 else {
            throw NSError(domain: "MQTTClient", code: 2, userInfo: [NSLocalizedDescriptionKey: "MQTT CONNACK rejected"])
        }
    }

    func publish(topic: String, payload: Data, retained: Bool) async throws {
        var packet = Data()
        packet.append(retained ? 0x31 : 0x30)
        var body = Data()
        body.appendMQTTString(topic)
        body.append(payload)
        packet.appendMQTTRemainingLength(body.count)
        packet.append(body)
        try await send(packet)
    }

    func disconnect() {
        let packet = Data([0xE0, 0x00])
        connection?.send(content: packet, completion: .contentProcessed { _ in })
        connection?.cancel()
        connection = nil
    }

    private func send(_ data: Data) async throws {
        guard let connection else { return }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            connection.send(content: data, completion: .contentProcessed { error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            })
        }
    }

    private func receive(minimum: Int) async throws -> Data {
        guard let connection else { return Data() }
        return try await withCheckedThrowingContinuation { continuation in
            connection.receive(minimumIncompleteLength: minimum, maximumLength: 2048) { data, _, _, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: data ?? Data())
                }
            }
        }
    }

    private func buildConnectPacket() -> Data {
        var body = Data()
        body.appendMQTTString("MQTT")
        body.append(0x04)

        var flags: UInt8 = 0x02
        if !settings.username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            flags |= 0x80
        }
        if !settings.password.isEmpty {
            flags |= 0x40
        }
        body.append(flags)
        body.append(contentsOf: [0x00, 0x1E])
        body.appendMQTTString(clientID)
        if !settings.username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body.appendMQTTString(settings.username.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        if !settings.password.isEmpty {
            body.appendMQTTString(settings.password)
        }

        var packet = Data([0x10])
        packet.appendMQTTRemainingLength(body.count)
        packet.append(body)
        return packet
    }
}

private extension Data {
    mutating func appendMQTTString(_ string: String) {
        let utf8 = Data(string.utf8)
        append(UInt8((utf8.count >> 8) & 0xFF))
        append(UInt8(utf8.count & 0xFF))
        append(utf8)
    }

    mutating func appendMQTTRemainingLength(_ value: Int) {
        var remainder = value
        repeat {
            var byte = UInt8(remainder % 128)
            remainder /= 128
            if remainder > 0 {
                byte |= 0x80
            }
            append(byte)
        } while remainder > 0
    }
}
