import SwiftUI

struct MQTTConfigView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var settings = MQTTSettings.load()

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("tcp://192.168.1.1:1883", text: $settings.serverURI)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section("Credentials") {
                    TextField("Username", text: $settings.username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Password", text: $settings.password)
                }
            }
            .navigationTitle("MQTT Config")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        settings.save()
                        dismiss()
                    }
                }
            }
        }
    }
}
