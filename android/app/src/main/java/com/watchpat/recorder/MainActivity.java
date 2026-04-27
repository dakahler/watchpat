package com.watchpat.recorder;

import android.Manifest;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.ScrollView;
import android.widget.TextView;

import androidx.core.content.FileProvider;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import org.json.JSONObject;

public class MainActivity extends AppCompatActivity implements WatchPatBleManager.Listener {

    private TextView tvStatus;
    private TextView tvDevice;
    private TextView tvFile;
    private TextView tvPackets;
    private TextView tvLog;
    private ScrollView scrollLog;
    private Button btnScan;
    private Button btnRecord;
    private Button btnShare;
    private Button btnAnalyze;
    private Button btnMqttConfig;

    private WatchPatBleManager bleManager;
    private boolean sessionReady = false; // true after SESSION_CONFIRM received

    private final ExecutorService analysisExecutor = Executors.newSingleThreadExecutor();

    private final ActivityResultLauncher<String[]> permissionLauncher =
            registerForActivityResult(
                    new ActivityResultContracts.RequestMultiplePermissions(),
                    result -> {
                        boolean allGranted = true;
                        for (Boolean granted : result.values()) {
                            if (!granted) { allGranted = false; break; }
                        }
                        if (allGranted) {
                            doStartScan();
                        } else {
                            appendLog("ERROR: Required permissions denied");
                            tvStatus.setText("Permissions denied — cannot scan");
                        }
                    });

    private final ActivityResultLauncher<String[]> openFileLauncher =
            registerForActivityResult(
                    new ActivityResultContracts.OpenDocument(),
                    uri -> {
                        if (uri != null) analyzeFromUri(uri);
                    });

    private final ActivityResultLauncher<Intent> enableBluetoothLauncher =
            registerForActivityResult(
                    new ActivityResultContracts.StartActivityForResult(),
                    result -> {
                        if (result.getResultCode() == RESULT_OK) {
                            checkPermissionsAndScan();
                        } else {
                            tvStatus.setText("Bluetooth is required");
                            appendLog("Bluetooth enable request denied");
                        }
                    });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        tvStatus  = findViewById(R.id.tv_status);
        tvDevice  = findViewById(R.id.tv_device);
        tvFile    = findViewById(R.id.tv_file);
        tvPackets = findViewById(R.id.tv_packets);
        tvLog     = findViewById(R.id.tv_log);
        scrollLog = findViewById(R.id.scroll_log);
        btnScan    = findViewById(R.id.btn_scan);
        btnRecord  = findViewById(R.id.btn_record);
        btnShare   = findViewById(R.id.btn_share);
        btnAnalyze = findViewById(R.id.btn_analyze);
        btnMqttConfig = findViewById(R.id.btn_mqtt_config);

        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }

        bleManager = new WatchPatBleManager(this, this);

        btnScan.setOnClickListener(v -> onScanButtonClicked());
        btnRecord.setOnClickListener(v -> onRecordButtonClicked());
        btnShare.setOnClickListener(v -> onShareButtonClicked());
        btnAnalyze.setOnClickListener(v -> openFileLauncher.launch(new String[]{"*/*"}));
        btnMqttConfig.setOnClickListener(v ->
                startActivity(new Intent(this, MqttConfigActivity.class)));
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (bleManager.isRecording()) bleManager.stopRecording();
        bleManager.disconnect();
    }

    // -----------------------------------------------------------------------
    // Button handlers
    // -----------------------------------------------------------------------

    private void onScanButtonClicked() {
        if (bleManager.isScanning()) {
            // Cancel active scan
            bleManager.stopScan();
        } else if (bleManager.isActiveOrReconnecting()) {
            // Disconnect from device (also cancels any in-progress reconnection)
            bleManager.stopRecording();
            bleManager.disconnect();
            btnScan.setText(getString(R.string.btn_scan));
            btnRecord.setEnabled(false);
            btnRecord.setText(getString(R.string.btn_start_recording));
            sessionReady = false;
            tvDevice.setText("");
            tvFile.setText("");
            tvPackets.setText("");
        } else {
            checkPermissionsAndScan();
        }
    }

    private void onRecordButtonClicked() {
        if (bleManager.isRecording()) {
            bleManager.stopRecording();
        } else {
            bleManager.startRecording();
        }
    }

    private void onShareButtonClicked() {
        String path = bleManager.getDatFilePath();
        if (path == null) return;
        File file = new File(path);
        if (!file.exists()) {
            appendLog("File not found: " + path);
            return;
        }
        Uri uri = FileProvider.getUriForFile(this,
                getPackageName() + ".fileprovider", file);
        Intent share = new Intent(Intent.ACTION_SEND);
        share.setType("application/octet-stream");
        share.putExtra(Intent.EXTRA_STREAM, uri);
        share.putExtra(Intent.EXTRA_SUBJECT, file.getName());
        share.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        startActivity(Intent.createChooser(share, "Share recording"));
    }

    // -----------------------------------------------------------------------
    // Permission handling
    // -----------------------------------------------------------------------

    private void checkPermissionsAndScan() {
        BluetoothManager bm = (BluetoothManager) getSystemService(BLUETOOTH_SERVICE);
        if (bm == null || bm.getAdapter() == null) {
            tvStatus.setText("Bluetooth not supported on this device");
            return;
        }
        if (!bm.getAdapter().isEnabled()) {
            Intent enableBt = new Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE);
            enableBluetoothLauncher.launch(enableBt);
            return;
        }

        List<String> needed = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (!hasPermission(Manifest.permission.BLUETOOTH_SCAN))
                needed.add(Manifest.permission.BLUETOOTH_SCAN);
            if (!hasPermission(Manifest.permission.BLUETOOTH_CONNECT))
                needed.add(Manifest.permission.BLUETOOTH_CONNECT);
        } else {
            if (!hasPermission(Manifest.permission.ACCESS_FINE_LOCATION))
                needed.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }

        if (needed.isEmpty()) {
            doStartScan();
        } else {
            permissionLauncher.launch(needed.toArray(new String[0]));
        }
    }

    private boolean hasPermission(String permission) {
        return ContextCompat.checkSelfPermission(this, permission)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void doStartScan() {
        sessionReady = false;
        tvDevice.setText("");
        tvFile.setText("");
        tvPackets.setText("");
        btnScan.setText(getString(R.string.btn_disconnect));
        btnRecord.setEnabled(false);
        btnShare.setVisibility(View.GONE);
        bleManager.startScan();
    }

    // -----------------------------------------------------------------------
    // WatchPatBleManager.Listener callbacks (all on main thread)
    // -----------------------------------------------------------------------

    @Override
    public void onStatusUpdate(String status) {
        tvStatus.setText(status);
        appendLog(status);
    }

    @Override
    public void onScanStopped() {
        btnScan.setText(getString(R.string.btn_scan));
    }

    @Override
    public void onDeviceFound(String name, String address) {
        tvDevice.setText(name + "  [" + address + "]");
        appendLog("Device found: " + name);
    }

    @Override
    public void onConnected(String deviceName) {
        tvStatus.setText("Connected to " + deviceName);
        appendLog("Connected — waiting for session confirm...");
    }

    @Override
    public void onReconnecting(int attempt, int maxAttempts) {
        // Keep button as "Disconnect" so user can abort
        btnScan.setText(getString(R.string.btn_disconnect));
        btnRecord.setEnabled(false);
        appendLog("Reconnecting... (" + attempt + "/" + maxAttempts + ")");
    }

    @Override
    public void onDisconnected() {
        tvStatus.setText("Disconnected");
        tvDevice.setText("");
        tvFile.setText("");
        tvPackets.setText("");
        btnScan.setText(getString(R.string.btn_scan));
        btnRecord.setEnabled(false);
        btnRecord.setText(getString(R.string.btn_start_recording));
        btnShare.setVisibility(View.GONE);
        sessionReady = false;
        appendLog("Disconnected");
    }

    @Override
    public void onSessionStarted(int serialNumber) {
        sessionReady = true;
        tvDevice.setText(tvDevice.getText() + "  SN: " + serialNumber);
        tvStatus.setText("Ready to record");
        btnRecord.setEnabled(true);
        appendLog("Session started — device serial: " + serialNumber);
        appendLog("Tap \"Start Recording\" to begin");
    }

    @Override
    public void onRecordingStarted(String filePath) {
        tvStatus.setText("Recording...");
        tvFile.setText(filePath);
        tvPackets.setText("Packets: 0");
        btnRecord.setText(getString(R.string.btn_stop_recording));
        btnRecord.setEnabled(true);
        btnShare.setVisibility(View.GONE);
        appendLog("Recording to: " + filePath);
        appendLog("NOTE: Device needs ~40 s warmup before data packets begin");
    }

    @Override
    public void onRecordingResumed(String filePath, int packetCount) {
        tvStatus.setText("Recording (resumed)...");
        tvPackets.setText("Packets: " + packetCount);
        btnRecord.setText(getString(R.string.btn_stop_recording));
        btnRecord.setEnabled(true);
        appendLog("Recording resumed — " + packetCount + " packets already written");
    }

    @Override
    public void onRecordingStopped(int packetCount) {
        tvStatus.setText("Recording stopped – " + packetCount + " packets saved");
        tvPackets.setText("Packets saved: " + packetCount);
        btnRecord.setText(getString(R.string.btn_start_recording));
        appendLog("Recording stopped — " + packetCount + " packets written");
        String path = bleManager.getDatFilePath();
        if (path != null) {
            appendLog("File: " + path);
            btnShare.setVisibility(View.VISIBLE);
            analyzeRecording(path);
        }
    }

    private void analyzeRecording(String path) {
        appendLog("Analyzing recording...");
        analysisExecutor.execute(() -> {
            runAnalysisAndPublish(path);
        });
    }

    private void analyzeFromUri(android.net.Uri uri) {
        appendLog("Loading file for analysis...");
        analysisExecutor.execute(() -> {
            File tmp = null;
            try {
                tmp = File.createTempFile("watchpat_", ".dat", getCacheDir());
                try (InputStream in = getContentResolver().openInputStream(uri);
                     FileOutputStream out = new FileOutputStream(tmp)) {
                    if (in == null) throw new IOException("Cannot open file");
                    byte[] buf = new byte[65536];
                    int n;
                    while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
                }
                runOnUiThread(() -> appendLog("Analyzing recording..."));
                runAnalysisAndPublish(tmp.getAbsolutePath());
            } catch (Exception e) {
                final String msg = "Load failed: " + e.getMessage();
                runOnUiThread(() -> appendLog(msg));
            } finally {
                if (tmp != null) tmp.delete();
            }
        });
    }

    private void runAnalysisAndPublish(String path) {
        try {
            Python py = Python.getInstance();
            PyObject module = py.getModule("watchpat_android");
            String jsonText = module.callAttr("analyze_json", path).toString();
            JSONObject payload = new JSONObject(jsonText);
            String summaryText = payload.optString("summary_text", "Analysis finished");
            runOnUiThread(() -> appendLog(summaryText));

            JSONObject summary = payload.optJSONObject("summary");
            if (summary != null && !summary.has("error")) {
                publishSummaryToMqtt(summary);
            }
        } catch (Exception e) {
            final String msg = "Analysis failed: " + e.getMessage();
            runOnUiThread(() -> appendLog(msg));
        }
    }

    private void publishSummaryToMqtt(JSONObject summary) {
        SharedPreferences prefs = getSharedPreferences(MqttConfigActivity.PREFS_NAME, Context.MODE_PRIVATE);
        String serverUri = prefs.getString(MqttConfigActivity.KEY_SERVER_URI, "");
        String username = prefs.getString(MqttConfigActivity.KEY_USERNAME, "");
        String password = prefs.getString(MqttConfigActivity.KEY_PASSWORD, "");
        String normalized = MqttPublisher.normalizeServerUri(serverUri);
        if (normalized.isEmpty()) {
            runOnUiThread(() -> appendLog(getString(R.string.mqtt_publish_disabled)));
            return;
        }

        try {
            MqttPublisher.publishSummary(normalized, username, password, summary.toString());
            runOnUiThread(() ->
                    appendLog("MQTT summary published to " + normalized + " topic " + MqttPublisher.DEFAULT_TOPIC));
        } catch (Exception e) {
            runOnUiThread(() ->
                    appendLog("MQTT publish failed: " + e.getMessage()));
        }
    }

    @Override
    public void onPacketReceived(int count) {
        tvPackets.setText("Packets: " + count);
    }

    @Override
    public void onError(String error) {
        tvStatus.setText("Error: " + error);
        appendLog("ERROR: " + error);
    }

    // -----------------------------------------------------------------------
    // Log helpers
    // -----------------------------------------------------------------------

    private void appendLog(String msg) {
        String ts = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date());
        String line = ts + "  " + msg + "\n";
        tvLog.append(line);
        scrollLog.post(() -> scrollLog.fullScroll(View.FOCUS_DOWN));
    }
}
