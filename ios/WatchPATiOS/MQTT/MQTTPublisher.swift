import Foundation

enum MQTTPublisher {
    static let defaultTopic = "watchpat/analysis"
    static let discoveryPrefix = "homeassistant"
    static let deviceID = "watchpat_ios_summary"

    private struct DiscoveryField {
        let key: String
        let label: String
        let unit: String?
        let stateClass: String?
    }

    private static let discoveryFields: [DiscoveryField] = [
        DiscoveryField(key: "ahi", label: "AHI", unit: "/hr", stateClass: "measurement"),
        DiscoveryField(key: "pahi", label: "pAHI", unit: "/hr", stateClass: "measurement"),
        DiscoveryField(key: "prdi", label: "pRDI", unit: "/hr", stateClass: "measurement"),
        DiscoveryField(key: "mean_spo2", label: "Mean SpO2", unit: "%", stateClass: "measurement"),
        DiscoveryField(key: "min_spo2", label: "Min SpO2", unit: "%", stateClass: "measurement"),
        DiscoveryField(key: "mean_hr_bpm", label: "Mean HR", unit: "bpm", stateClass: "measurement"),
        DiscoveryField(key: "max_hr_bpm", label: "Max HR", unit: "bpm", stateClass: "measurement"),
        DiscoveryField(key: "duration_minutes", label: "Duration", unit: "min", stateClass: "measurement"),
        DiscoveryField(key: "packet_count", label: "Packet Count", unit: "packets", stateClass: "measurement"),
        DiscoveryField(key: "apnea_events", label: "Apnea Events", unit: "events", stateClass: "total"),
        DiscoveryField(key: "central_events", label: "Central Events", unit: "events", stateClass: "total"),
    ]

    static func publishSummary(settings: MQTTSettings, summary: AnalysisSummaryFields) async throws {
        let client = MQTTClient(settings: settings, clientID: "watchpat-ios-\(UUID().uuidString)")
        try await client.connect()
        defer { client.disconnect() }

        try await publishDiscovery(client: client)
        let payload = try JSONEncoder().encode(summary)
        try await client.publish(topic: defaultTopic, payload: payload, retained: true)
    }

    private static func publishDiscovery(client: MQTTClient) async throws {
        for field in discoveryFields {
            var payload: [String: Any] = [
                "name": "WatchPAT \(field.label)",
                "unique_id": "\(deviceID)_\(field.key)",
                "state_topic": defaultTopic,
                "value_template": "{{ value_json.\(field.key) }}",
                "device": [
                    "identifiers": [deviceID],
                    "name": "WatchPAT iOS Summary",
                    "manufacturer": "WatchPAT",
                    "model": "iOS Recorder",
                ],
            ]
            if let unit = field.unit {
                payload["unit_of_measurement"] = unit
            }
            if let stateClass = field.stateClass {
                payload["state_class"] = stateClass
            }
            let topic = "\(discoveryPrefix)/sensor/\(deviceID)/\(field.key)/config"
            let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
            try await client.publish(topic: topic, payload: data, retained: true)
        }
    }
}
