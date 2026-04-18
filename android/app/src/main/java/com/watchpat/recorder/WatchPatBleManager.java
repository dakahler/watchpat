package com.watchpat.recorder;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.watchpat.recorder.kaitai.WatchpatPacket;
import io.kaitai.struct.ByteBufferKaitaiStream;
import io.kaitai.struct.KaitaiStream;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.LinkedList;
import java.util.List;
import java.util.Locale;
import java.util.Queue;
import java.util.UUID;

/**
 * Manages BLE connection to a WatchPAT ONE device and records data to a .dat file.
 *
 * Flow:
 *   startScan()  -> finds ITAMAR_* device -> connect() -> onServicesDiscovered()
 *     -> enable TX notifications -> onDescriptorWrite() -> sendSessionStart()
 *     -> SESSION_CONFIRM -> startRecording() -> sendStartAcquisition()
 *     -> DATA_PACKETs written to file -> stopRecording() -> sendStopAcquisition()
 */
public class WatchPatBleManager {

    private static final String TAG = "WatchPatBle";
    private static final long SCAN_TIMEOUT_MS      = 30_000;
    private static final int  MAX_RECONNECT_ATTEMPTS = 60;   // 60 × 5s = 5 min window
    private static final long RECONNECT_DELAY_MS     = 5_000;

    // -----------------------------------------------------------------------
    // Listener interface for UI callbacks (always called on main thread)
    // -----------------------------------------------------------------------
    public interface Listener {
        void onStatusUpdate(String status);
        void onScanStopped();
        void onDeviceFound(String name, String address);
        void onConnected(String deviceName);
        void onReconnecting(int attempt, int maxAttempts);
        void onDisconnected();
        void onSessionStarted(int serialNumber);
        void onRecordingStarted(String filePath);
        void onRecordingResumed(String filePath, int packetCount);
        void onRecordingStopped(int packetCount);
        void onPacketReceived(int count);
        void onError(String error);
    }

    private final Context context;
    private final Listener listener;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private BluetoothAdapter bluetoothAdapter;
    private BluetoothLeScanner scanner;
    private ScanCallback scanCallback;

    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic rxChar; // write to device
    private BluetoothGattCharacteristic txChar; // notifications from device

    private final WatchPatProtocol.PacketReassembler reassembler =
            new WatchPatProtocol.PacketReassembler();

    // BLE write queue — ensures we wait for onCharacteristicWrite before next send
    private final Queue<byte[]> writeQueue = new LinkedList<>();
    private volatile boolean writeInProgress = false;

    private volatile boolean isConnected          = false;
    private volatile boolean isScanning           = false;
    private volatile boolean isRecording          = false;
    private volatile boolean isReconnecting       = false;
    private volatile boolean intentionalDisconnect = false;
    private volatile boolean wasRecording         = false; // recording was active at disconnect
    private int reconnectAttempts = 0;
    private int packetCount  = 0;
    private int deviceSerial = 0;

    private BluetoothDevice connectedDevice;
    private String          connectedDeviceName;

    private FileOutputStream datFile;
    private String datFilePath;

    public WatchPatBleManager(Context context, Listener listener) {
        this.context  = context;
        this.listener = listener;
        BluetoothManager bm =
                (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
        if (bm != null) bluetoothAdapter = bm.getAdapter();
    }

    // -----------------------------------------------------------------------
    // Scanning
    // -----------------------------------------------------------------------

    public void startScan() {
        if (bluetoothAdapter == null || !bluetoothAdapter.isEnabled()) {
            notifyError("Bluetooth is not enabled");
            return;
        }
        scanner = bluetoothAdapter.getBluetoothLeScanner();
        if (scanner == null) {
            notifyError("BLE scanner unavailable");
            return;
        }

        isScanning = true;
        notifyStatus("Scanning for ITAMAR_* devices...");

        ScanSettings settings = new ScanSettings.Builder()
                .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
                .build();

        scanCallback = new ScanCallback() {
            @Override
            public void onScanResult(int callbackType, ScanResult result) {
                String name = getDeviceName(result);
                if (name != null && name.startsWith("ITAMAR_")) {
                    Log.d(TAG, "Found: " + name + " @ " + result.getDevice().getAddress());
                    // Stop scan quietly — we're transitioning to connect, not cancelling
                    stopScanInternal();
                    notifyStatus("Found device: " + name);
                    mainHandler.post(() -> listener.onDeviceFound(name, result.getDevice().getAddress()));
                    connectToDevice(result.getDevice(), name);
                }
            }

            @Override
            public void onScanFailed(int errorCode) {
                stopScanInternal();
                notifyError("Scan failed (error " + errorCode + ")");
                mainHandler.post(() -> listener.onScanStopped());
            }
        };

        scanner.startScan(null, settings, scanCallback);
        mainHandler.postDelayed(() -> {
            if (isScanning) {
                stopScanInternal();
                notifyStatus("Scan timed out — no device found");
                mainHandler.post(() -> listener.onScanStopped());
            }
        }, SCAN_TIMEOUT_MS);
    }

    /** Stops the BLE scan and notifies the listener (manual cancel). */
    public void stopScan() {
        if (isScanning) {
            stopScanInternal();
            mainHandler.post(() -> listener.onScanStopped());
        }
    }

    /** Stops the BLE scan hardware without firing onScanStopped (internal use). */
    private void stopScanInternal() {
        isScanning = false;
        if (scanner != null && scanCallback != null) {
            try {
                scanner.stopScan(scanCallback);
            } catch (Exception ignored) {}
            scanCallback = null;
        }
    }

    private String getDeviceName(ScanResult result) {
        if (result.getScanRecord() != null && result.getScanRecord().getDeviceName() != null) {
            return result.getScanRecord().getDeviceName();
        }
        return result.getDevice().getName();
    }

    // -----------------------------------------------------------------------
    // Connection
    // -----------------------------------------------------------------------

    private void connectToDevice(BluetoothDevice device, String name) {
        connectedDevice     = device;
        connectedDeviceName = name;
        notifyStatus((isReconnecting ? "Reconnecting" : "Connecting") + " to " + name + "...");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            gatt = device.connectGatt(context, false, gattCallback,
                    BluetoothDevice.TRANSPORT_LE);
        } else {
            gatt = device.connectGatt(context, false, gattCallback);
        }
    }

    public void disconnect() {
        intentionalDisconnect = true;
        isReconnecting = false;
        reconnectAttempts = 0;
        isRecording = false;
        wasRecording = false;
        connectedDevice = null;
        connectedDeviceName = null;
        closeFile();
        if (gatt != null) {
            gatt.disconnect();
            // close() is called after STATE_DISCONNECTED fires
        }
    }

    // -----------------------------------------------------------------------
    // GATT callbacks
    // -----------------------------------------------------------------------

    private final BluetoothGattCallback gattCallback = new BluetoothGattCallback() {

        @Override
        public void onConnectionStateChange(BluetoothGatt g, int status, int newState) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                notifyStatus("Connected — discovering services...");
                g.discoverServices();
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                Log.d(TAG, "Disconnected (status=" + status + " intentional=" + intentionalDisconnect + ")");
                rxChar = null;
                txChar = null;
                isConnected = false;
                reassembler.reset();
                synchronized (WatchPatBleManager.this) {
                    writeQueue.clear();
                    writeInProgress = false;
                }
                g.close();

                if (intentionalDisconnect || connectedDevice == null) {
                    // User-initiated — full teardown
                    intentionalDisconnect = false;
                    isRecording = false;
                    wasRecording = false;
                    closeFile();
                    mainHandler.post(() -> listener.onDisconnected());
                } else {
                    // Unexpected disconnect — keep file open and attempt reconnect
                    wasRecording = isRecording;
                    isRecording = false;
                    gatt = null;
                    scheduleReconnect();
                }
            }
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt g, int status) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                notifyError("Service discovery failed: " + status);
                return;
            }

            BluetoothGattService nus = g.getService(
                    UUID.fromString(WatchPatProtocol.NUS_SERVICE_UUID));
            if (nus == null) {
                notifyError("NUS service not found on device");
                return;
            }

            rxChar = nus.getCharacteristic(UUID.fromString(WatchPatProtocol.NUS_RX_CHAR_UUID));
            txChar = nus.getCharacteristic(UUID.fromString(WatchPatProtocol.NUS_TX_CHAR_UUID));

            if (rxChar == null || txChar == null) {
                notifyError("NUS RX/TX characteristics not found");
                return;
            }

            // Enable notifications on TX characteristic
            g.setCharacteristicNotification(txChar, true);
            BluetoothGattDescriptor cccd = txChar.getDescriptor(
                    UUID.fromString(WatchPatProtocol.CCCD_UUID));
            if (cccd != null) {
                writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
            } else {
                notifyError("CCCD descriptor not found on TX characteristic");
            }
        }

        @Override
        public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor descriptor,
                                      int status) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                isConnected = true;
                reconnectAttempts = 0;
                if (isReconnecting) {
                    Log.d(TAG, "Reconnected — resuming session");
                    notifyStatus("Reconnected — resuming session...");
                    // Use mode 4 (resume) so device knows this continues a prior session
                    sendChunks(WatchPatProtocol.buildSessionStart(4));
                } else {
                    Log.d(TAG, "Notifications enabled — starting session");
                    notifyStatus("Notifications enabled — starting session...");
                    String deviceName = g.getDevice().getName();
                    mainHandler.post(() -> listener.onConnected(deviceName));
                    sendChunks(WatchPatProtocol.buildSessionStart(1));
                }
            } else {
                notifyError("Failed to enable notifications: " + status);
            }
        }

        @Override
        public void onCharacteristicWrite(BluetoothGatt g,
                                          BluetoothGattCharacteristic characteristic,
                                          int status) {
            synchronized (WatchPatBleManager.this) {
                writeInProgress = false;
            }
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.w(TAG, "Characteristic write failed: " + status);
            }
            drainWriteQueue();
        }

        // Called on Android < 13
        @SuppressWarnings("deprecation")
        @Override
        public void onCharacteristicChanged(BluetoothGatt g,
                                             BluetoothGattCharacteristic characteristic) {
            byte[] value = characteristic.getValue();
            if (value != null) handleNotification(value);
        }

        // Called on Android 13+
        @Override
        public void onCharacteristicChanged(BluetoothGatt g,
                                             BluetoothGattCharacteristic characteristic,
                                             byte[] value) {
            if (value != null) handleNotification(value);
        }
    };

    // -----------------------------------------------------------------------
    // Notification / packet handling
    // -----------------------------------------------------------------------

    private void handleNotification(byte[] chunk) {
        reassembler.feed(chunk, (opcode, packetId, fullPacket) -> {
            Log.d(TAG, String.format("RX opcode=0x%04X id=%d len=%d",
                    opcode, packetId, fullPacket.length));

            switch (opcode) {
                case WatchPatProtocol.RESP_SESSION_CONFIRM:
                    handleSessionConfirm(packetId, fullPacket);
                    break;

                case WatchPatProtocol.RESP_ACK:
                    Log.d(TAG, "ACK received for id=" + packetId);
                    break;

                case WatchPatProtocol.RESP_DATA_PACKET:
                    handleDataPacket(fullPacket);
                    break;

                case WatchPatProtocol.RESP_END_OF_TEST:
                    notifyStatus("End-of-test received from device");
                    break;

                case WatchPatProtocol.RESP_ERROR_STATUS:
                    notifyStatus("Error status received from device");
                    break;

                default:
                    Log.d(TAG, String.format("Unhandled response opcode 0x%04X", opcode));
                    break;
            }

            sendChunks(WatchPatProtocol.buildAck(opcode, 0, packetId));
        });
    }

    private void handleSessionConfirm(int packetId, byte[] packet) {
        byte[] payload = WatchPatProtocol.extractPayload(packet);
        try {
            WatchpatPacket.SessionConfirmPayload scp =
                new WatchpatPacket.SessionConfirmPayload(
                    new ByteBufferKaitaiStream(payload), null, null);
            Long serial = scp.serialNumber();
            if (serial != null) {
                deviceSerial = serial.intValue();
            }
        } catch (Exception e) {
            Log.w(TAG, "Failed to parse session confirm: " + e.getMessage());
        }
        Log.d(TAG, "Session confirmed — serial=" + deviceSerial + " wasRecording=" + wasRecording);

        if (wasRecording && datFile != null) {
            // Reconnected mid-recording — restart acquisition and append to same file
            wasRecording = false;
            isReconnecting = false;
            isRecording = true;
            sendChunks(WatchPatProtocol.buildStartAcquisition());
            String path = datFilePath;
            int count = packetCount;
            notifyStatus("Recording resumed — " + count + " packets so far");
            mainHandler.post(() -> listener.onRecordingResumed(path, count));
        } else {
            // Fresh connection — wait for user to press Start
            isReconnecting = false;
            wasRecording = false;
            notifyStatus("Session confirmed — serial: " + deviceSerial);
            int serial = deviceSerial;
            mainHandler.post(() -> listener.onSessionStarted(serial));
        }
    }

    private void handleDataPacket(byte[] fullPacket) {
        if (!isRecording || datFile == null) return;
        try {
            // Write 4-byte little-endian length prefix + full packet bytes
            int len = fullPacket.length;
            datFile.write(new byte[]{
                    (byte) (len & 0xFF),
                    (byte) ((len >> 8) & 0xFF),
                    (byte) ((len >> 16) & 0xFF),
                    (byte) ((len >> 24) & 0xFF)
            });
            datFile.write(fullPacket);
            packetCount++;

            if (packetCount % 10 == 0) {
                int count = packetCount;
                mainHandler.post(() -> listener.onPacketReceived(count));
            }
        } catch (IOException e) {
            notifyError("File write error: " + e.getMessage());
        }
    }

    // -----------------------------------------------------------------------
    // Reconnection
    // -----------------------------------------------------------------------

    private void scheduleReconnect() {
        reconnectAttempts++;
        if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
            Log.d(TAG, "Max reconnect attempts reached — giving up");
            reconnectAttempts = 0;
            isReconnecting = false;
            wasRecording = false;
            closeFile();
            notifyStatus("Could not reconnect after " + MAX_RECONNECT_ATTEMPTS + " attempts");
            mainHandler.post(() -> listener.onDisconnected());
            return;
        }

        isReconnecting = true;
        int attempt = reconnectAttempts;
        mainHandler.post(() -> listener.onReconnecting(attempt, MAX_RECONNECT_ATTEMPTS));
        notifyStatus("Out of range — reconnecting (" + attempt + "/" + MAX_RECONNECT_ATTEMPTS + ")");

        mainHandler.postDelayed(() -> {
            if (intentionalDisconnect || connectedDevice == null) return;
            connectToDevice(connectedDevice, connectedDeviceName);
        }, RECONNECT_DELAY_MS);
    }

    // -----------------------------------------------------------------------
    // Recording control (called from UI thread)
    // -----------------------------------------------------------------------

    public void startRecording() {
        if (!isConnected) {
            notifyError("Not connected to device");
            return;
        }
        if (isRecording) return;

        // Resolve output directory (app-specific external storage, no permission required)
        File storageDir = context.getExternalFilesDir(null);
        if (storageDir == null) storageDir = context.getFilesDir();

        String ts = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());
        String filename = deviceSerial > 0
                ? "watchpat_" + deviceSerial + "_" + ts + ".dat"
                : "watchpat_" + ts + ".dat";
        File file = new File(storageDir, filename);

        try {
            datFile = new FileOutputStream(file);
            datFilePath = file.getAbsolutePath();
            packetCount = 0;
            isRecording = true;
        } catch (IOException e) {
            notifyError("Cannot create output file: " + e.getMessage());
            return;
        }

        sendChunks(WatchPatProtocol.buildStartAcquisition());
        String path = datFilePath;
        mainHandler.post(() -> listener.onRecordingStarted(path));
        notifyStatus("Recording started — " + filename);
    }

    public void stopRecording() {
        if (!isRecording) return;
        isRecording = false;
        sendChunks(WatchPatProtocol.buildStopAcquisition());
        int count = packetCount;
        closeFile();
        mainHandler.post(() -> listener.onRecordingStopped(count));
        notifyStatus("Recording stopped — " + count + " packets saved");
    }

    private void closeFile() {
        if (datFile != null) {
            try {
                datFile.flush();
                datFile.close();
            } catch (IOException ignored) {}
            datFile = null;
        }
    }

    // -----------------------------------------------------------------------
    // BLE write queue
    // -----------------------------------------------------------------------

    private synchronized void sendChunks(List<byte[]> chunks) {
        writeQueue.addAll(chunks);
        drainWriteQueue();
    }

    private synchronized void drainWriteQueue() {
        if (writeInProgress || writeQueue.isEmpty() || gatt == null || rxChar == null) return;
        byte[] chunk = writeQueue.poll();
        if (chunk == null) return;
        writeInProgress = true;
        writeBleCharacteristic(chunk);
    }

    @SuppressWarnings("deprecation")
    private void writeBleCharacteristic(byte[] value) {
        if (gatt == null || rxChar == null) {
            synchronized (this) { writeInProgress = false; }
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            int result = gatt.writeCharacteristic(
                    rxChar, value, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE);
            if (result != BluetoothGatt.GATT_SUCCESS) {
                Log.w(TAG, "writeCharacteristic returned " + result);
                synchronized (this) { writeInProgress = false; }
                drainWriteQueue();
            }
        } else {
            rxChar.setValue(value);
            rxChar.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE);
            boolean ok = gatt.writeCharacteristic(rxChar);
            if (!ok) {
                Log.w(TAG, "writeCharacteristic returned false");
                synchronized (this) { writeInProgress = false; }
                drainWriteQueue();
            }
        }
    }

    @SuppressWarnings("deprecation")
    private void writeDescriptor(BluetoothGattDescriptor descriptor, byte[] value) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            gatt.writeDescriptor(descriptor, value);
        } else {
            descriptor.setValue(value);
            gatt.writeDescriptor(descriptor);
        }
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private void notifyStatus(String msg) {
        Log.d(TAG, msg);
        mainHandler.post(() -> listener.onStatusUpdate(msg));
    }

    private void notifyError(String msg) {
        Log.e(TAG, msg);
        mainHandler.post(() -> listener.onError(msg));
    }

    public boolean isConnected()           { return isConnected; }
    public boolean isScanning()            { return isScanning; }
    public boolean isRecording()           { return isRecording; }
    public boolean isReconnecting()        { return isReconnecting; }
    /** True when there is an active session or a reconnect is in progress. */
    public boolean isActiveOrReconnecting() { return isConnected || isReconnecting; }
    public String  getDatFilePath()        { return datFilePath; }
}
