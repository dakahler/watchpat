import CoreBluetooth
import Foundation

@MainActor
protocol WatchPatBLEManagerDelegate: AnyObject {
    func didUpdateStatus(_ status: String)
    func didFindDevice(_ name: String, identifier: UUID)
    func didConnect(deviceName: String)
    func didStartSession(serialNumber: Int)
    func didStartRecording(fileURL: URL)
    func didStopRecording(packetCount: Int, fileURL: URL?)
    func didReceivePackets(_ count: Int)
    func didDisconnect()
    func didError(_ message: String)
}

@MainActor
final class WatchPatBLEManager: NSObject {
    weak var delegate: WatchPatBLEManagerDelegate?

    private lazy var central = CBCentralManager(delegate: self, queue: .main)
    private var peripheral: CBPeripheral?
    private var rxCharacteristic: CBCharacteristic?
    private var txCharacteristic: CBCharacteristic?
    private var writeQueue: [Data] = []
    private var writeInProgress = false
    private let reassembler = WatchPatProtocol.PacketReassembler()

    private(set) var isScanning = false
    private(set) var isRecording = false
    private(set) var packetCount = 0
    private(set) var datFileURL: URL?
    private var fileHandle: FileHandle?

    func toggleScanOrDisconnect() {
        if isScanning {
            central.stopScan()
            isScanning = false
            delegate?.didUpdateStatus("Scan stopped")
            return
        }
        if peripheral != nil {
            disconnect()
            return
        }
        startScan()
    }

    func toggleRecording() {
        isRecording ? stopRecording() : startRecording()
    }

    func disconnect() {
        isRecording = false
        closeFile()
        if let peripheral {
            central.cancelPeripheralConnection(peripheral)
        } else {
            delegate?.didDisconnect()
        }
        peripheral = nil
        rxCharacteristic = nil
        txCharacteristic = nil
        reassembler.reset()
    }

    private func startScan() {
        guard central.state == .poweredOn else {
            delegate?.didError("Bluetooth is not ready")
            return
        }
        isScanning = true
        delegate?.didUpdateStatus("Scanning for ITAMAR_* devices...")
        central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    }

    private func startRecording() {
        guard peripheral != nil else { return }
        do {
            let url = try makeRecordingURL()
            FileManager.default.createFile(atPath: url.path, contents: nil)
            fileHandle = try FileHandle(forWritingTo: url)
            datFileURL = url
            packetCount = 0
            isRecording = true
            sendChunks(WatchPatProtocol.buildStartAcquisition())
            delegate?.didStartRecording(fileURL: url)
        } catch {
            delegate?.didError("Failed to create .dat file: \(error.localizedDescription)")
        }
    }

    private func stopRecording() {
        isRecording = false
        sendChunks(WatchPatProtocol.buildStopAcquisition())
        closeFile()
        delegate?.didStopRecording(packetCount: packetCount, fileURL: datFileURL)
    }

    private func closeFile() {
        try? fileHandle?.close()
        fileHandle = nil
    }

    private func makeRecordingURL() throws -> URL {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let name = "watchpat_\(formatter.string(from: Date()).replacingOccurrences(of: ":", with: "-")).dat"
        let dir = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        return dir.appendingPathComponent(name)
    }

    private func sendChunks(_ chunks: [Data]) {
        writeQueue.append(contentsOf: chunks)
        drainWriteQueue()
    }

    private func drainWriteQueue() {
        guard !writeInProgress, let peripheral, let rxCharacteristic, !writeQueue.isEmpty else {
            return
        }
        writeInProgress = true
        let chunk = writeQueue.removeFirst()
        peripheral.writeValue(chunk, for: rxCharacteristic, type: .withResponse)
    }

    private func handleNotification(_ value: Data) {
        reassembler.feed(value) { [weak self] opcode, packetID, packet in
            guard let self else { return }
            switch opcode {
            case WatchPatProtocol.respSessionConfirm:
                let payload = packet.dropFirst(WatchPatProtocol.headerSize)
                let serial: Int
                if payload.count >= 58 {
                    serial = Int(payload.withUnsafeBytes { raw in
                        raw.loadUnaligned(fromByteOffset: 54, as: UInt32.self)
                    }.littleEndian)
                } else {
                    serial = 0
                }
                self.delegate?.didStartSession(serialNumber: serial)
            case WatchPatProtocol.respDataPacket:
                self.handleDataPacket(packet)
            case WatchPatProtocol.respEndOfTest:
                self.delegate?.didUpdateStatus("End-of-test received")
            case WatchPatProtocol.respErrorStatus:
                self.delegate?.didUpdateStatus("Error status received")
            default:
                break
            }
            self.sendChunks(WatchPatProtocol.buildAck(responseOpcode: opcode, responseID: packetID))
        }
    }

    private func handleDataPacket(_ packet: Data) {
        guard isRecording, let fileHandle else { return }
        var length = UInt32(packet.count).littleEndian
        let prefix = withUnsafeBytes(of: &length) { Data($0) }
        do {
            try fileHandle.write(contentsOf: prefix)
            try fileHandle.write(contentsOf: packet)
            packetCount += 1
            if packetCount % 10 == 0 {
                delegate?.didReceivePackets(packetCount)
            }
        } catch {
            delegate?.didError("File write failed: \(error.localizedDescription)")
        }
    }
}

@MainActor
extension WatchPatBLEManager: CBCentralManagerDelegate, CBPeripheralDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state != .poweredOn {
            delegate?.didUpdateStatus("Bluetooth unavailable")
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let name = advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name ?? ""
        guard name.hasPrefix("ITAMAR_") else { return }
        isScanning = false
        central.stopScan()
        self.peripheral = peripheral
        peripheral.delegate = self
        delegate?.didFindDevice(name, identifier: peripheral.identifier)
        delegate?.didUpdateStatus("Connecting to \(name)...")
        central.connect(peripheral)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        delegate?.didConnect(deviceName: peripheral.name ?? "WatchPAT")
        delegate?.didUpdateStatus("Connected - discovering services...")
        peripheral.discoverServices([WatchPatProtocol.nusServiceUUID])
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        isScanning = false
        isRecording = false
        closeFile()
        self.peripheral = nil
        rxCharacteristic = nil
        txCharacteristic = nil
        delegate?.didDisconnect()
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error {
            delegate?.didError("Service discovery failed: \(error.localizedDescription)")
            return
        }
        for service in peripheral.services ?? [] where service.uuid == WatchPatProtocol.nusServiceUUID {
            peripheral.discoverCharacteristics([WatchPatProtocol.nusRXCharUUID, WatchPatProtocol.nusTXCharUUID], for: service)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error {
            delegate?.didError("Characteristic discovery failed: \(error.localizedDescription)")
            return
        }
        for characteristic in service.characteristics ?? [] {
            if characteristic.uuid == WatchPatProtocol.nusRXCharUUID {
                rxCharacteristic = characteristic
            } else if characteristic.uuid == WatchPatProtocol.nusTXCharUUID {
                txCharacteristic = characteristic
                peripheral.setNotifyValue(true, for: characteristic)
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if let error {
            delegate?.didError("Notification setup failed: \(error.localizedDescription)")
            return
        }
        if characteristic.uuid == WatchPatProtocol.nusTXCharUUID, characteristic.isNotifying {
            delegate?.didUpdateStatus("Notifications enabled - starting session...")
            sendChunks(WatchPatProtocol.buildSessionStart())
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error {
            delegate?.didError("Notification read failed: \(error.localizedDescription)")
            return
        }
        guard let value = characteristic.value else { return }
        handleNotification(value)
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        writeInProgress = false
        if let error {
            delegate?.didError("Write failed: \(error.localizedDescription)")
        }
        drainWriteQueue()
    }
}
