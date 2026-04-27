import Foundation
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class AppModel: ObservableObject {
    @Published var status = "Not connected"
    @Published var deviceText = ""
    @Published var fileText = ""
    @Published var packetText = ""
    @Published var logText = ""
    @Published var isConnected = false
    @Published var isRecording = false
    @Published var showMQTTConfig = false
    @Published var isAnalyzing = false
    @Published var selectedRecordingURL: URL?

    let bleManager = WatchPatBLEManager()
    private let analyzer = WatchPatAnalyzer()

    init() {
        bleManager.delegate = self
    }

    var scanButtonTitle: String {
        isConnected || bleManager.isScanning ? "Disconnect" : "Scan"
    }

    var recordButtonTitle: String {
        isRecording ? "Stop Recording" : "Start Recording"
    }

    var canRecord: Bool {
        isConnected
    }

    func onScanTapped() {
        bleManager.toggleScanOrDisconnect()
    }

    func onRecordTapped() {
        bleManager.toggleRecording()
    }

    func analyzeImportedFile(_ url: URL) {
        appendLog("Loading file for analysis...")
        Task {
            await analyze(url: url)
        }
    }

    func analyzeCurrentRecording() {
        guard let url = selectedRecordingURL else { return }
        Task {
            await analyze(url: url)
        }
    }

    private func analyze(url: URL) async {
        isAnalyzing = true
        appendLog("Analyzing recording...")
        do {
            let envelope = try analyzer.analyze(fileURL: url)
            appendLog(envelope.summaryText)
            try await maybePublish(summary: envelope.summary)
        } catch {
            appendLog("Analysis failed: \(error.localizedDescription)")
        }
        isAnalyzing = false
    }

    private func maybePublish(summary: AnalysisSummaryFields) async throws {
        let settings = MQTTSettings.load()
        guard settings.normalizedHostPort != nil else {
            appendLog("MQTT publishing disabled: no server configured")
            return
        }
        try await MQTTPublisher.publishSummary(settings: settings, summary: summary)
        appendLog("MQTT summary published to \(MQTTPublisher.defaultTopic)")
    }

    func appendLog(_ line: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        let stamped = "\(formatter.string(from: Date()))  \(line)"
        logText = logText.isEmpty ? stamped : "\(logText)\n\(stamped)"
    }
}

extension AppModel: WatchPatBLEManagerDelegate {
    func didUpdateStatus(_ status: String) {
        self.status = status
        appendLog(status)
    }

    func didFindDevice(_ name: String, identifier: UUID) {
        deviceText = "\(name)  [\(identifier.uuidString)]"
        appendLog("Device found: \(name)")
    }

    func didConnect(deviceName: String) {
        isConnected = true
        status = "Connected to \(deviceName)"
        appendLog("Connected - waiting for session confirm...")
    }

    func didStartSession(serialNumber: Int) {
        isConnected = true
        status = "Ready to record"
        if serialNumber > 0 {
            deviceText = deviceText.isEmpty ? "SN: \(serialNumber)" : "\(deviceText)  SN: \(serialNumber)"
        }
        appendLog("Session started - device serial: \(serialNumber)")
    }

    func didStartRecording(fileURL: URL) {
        isRecording = true
        selectedRecordingURL = fileURL
        status = "Recording..."
        fileText = fileURL.path
        packetText = "Packets: 0"
        appendLog("Recording to: \(fileURL.path)")
        appendLog("NOTE: Device needs ~40 s warmup before data packets begin")
    }

    func didStopRecording(packetCount: Int, fileURL: URL?) {
        isRecording = false
        status = "Recording stopped - \(packetCount) packets saved"
        packetText = "Packets saved: \(packetCount)"
        appendLog("Recording stopped - \(packetCount) packets written")
        if let fileURL {
            selectedRecordingURL = fileURL
            appendLog("File: \(fileURL.path)")
            Task {
                await analyze(url: fileURL)
            }
        }
    }

    func didReceivePackets(_ count: Int) {
        packetText = "Packets: \(count)"
    }

    func didDisconnect() {
        isConnected = false
        isRecording = false
        status = "Disconnected"
        appendLog("Disconnected")
    }

    func didError(_ message: String) {
        status = "Error: \(message)"
        appendLog("ERROR: \(message)")
    }
}
