import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showingImporter = false

    var body: some View {
        VStack(spacing: 12) {
            Text("WatchPAT Recorder")
                .frame(maxWidth: .infinity, alignment: .leading)
                .font(.system(size: 28, weight: .bold))
                .foregroundStyle(Color(red: 0.08, green: 0.4, blue: 0.75))

            card {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Status")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    Text(model.status)
                        .font(.body)
                    if !model.deviceText.isEmpty {
                        Text(model.deviceText)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if !model.fileText.isEmpty {
                        Text(model.fileText)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                    if !model.packetText.isEmpty {
                        Text(model.packetText)
                            .font(.caption)
                            .foregroundStyle(Color.green)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            card {
                ScrollView {
                    Text(model.logText)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(Color(red: 0.22, green: 0.29, blue: 0.31))
                        .textSelection(.enabled)
                }
            }
            .frame(maxHeight: .infinity)

            HStack(spacing: 8) {
                Button(model.scanButtonTitle) {
                    model.onScanTapped()
                }
                .buttonStyle(.borderedProminent)
                .frame(maxWidth: .infinity)

                Button(model.recordButtonTitle) {
                    model.onRecordTapped()
                }
                .buttonStyle(.bordered)
                .disabled(!model.canRecord)
                .frame(maxWidth: .infinity)

                if let url = model.selectedRecordingURL {
                    ShareLink(item: url) {
                        Text("Share")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                } else {
                    Button("Share") {}
                        .buttonStyle(.bordered)
                        .disabled(true)
                        .frame(maxWidth: .infinity)
                }
            }

            Button("Analyze File") {
                showingImporter = true
            }
            .buttonStyle(.bordered)
            .frame(maxWidth: .infinity)

            Button("MQTT Config") {
                model.showMQTTConfig = true
            }
            .buttonStyle(.bordered)
            .frame(maxWidth: .infinity)
        }
        .padding(16)
        .background(Color(red: 0.96, green: 0.96, blue: 0.96))
        .fileImporter(
            isPresented: $showingImporter,
            allowedContentTypes: [UTType.data, UTType(filenameExtension: "dat") ?? .data]
        ) { result in
            if case .success(let url) = result {
                model.analyzeImportedFile(url)
            }
        }
        .sheet(isPresented: $model.showMQTTConfig) {
            MQTTConfigView()
        }
    }

    private func card<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .padding(12)
            .background(.white)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .shadow(color: .black.opacity(0.12), radius: 6, x: 0, y: 2)
    }
}
