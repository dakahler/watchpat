package com.watchpat.recorder;

import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

public final class MqttPublisher {

    public static final String DEFAULT_TOPIC = "watchpat/analysis";
    public static final String DISCOVERY_PREFIX = "homeassistant";
    private static final String DEVICE_ID = "watchpat_android_summary";

    private MqttPublisher() {}

    private static final class DiscoveryField {
        final String key;
        final String label;
        final String unit;
        final String stateClass;

        DiscoveryField(String key, String label, String unit, String stateClass) {
            this.key = key;
            this.label = label;
            this.unit = unit;
            this.stateClass = stateClass;
        }
    }

    private static List<DiscoveryField> discoveryFields() {
        return Arrays.asList(
                new DiscoveryField("ahi", "AHI", "/hr", "measurement"),
                new DiscoveryField("pahi", "pAHI", "/hr", "measurement"),
                new DiscoveryField("prdi", "pRDI", "/hr", "measurement"),
                new DiscoveryField("awake_pct", "Awake", "%", "measurement"),
                new DiscoveryField("light_pct", "Light", "%", "measurement"),
                new DiscoveryField("deep_pct", "Deep", "%", "measurement"),
                new DiscoveryField("rem_pct", "REM", "%", "measurement"),
                new DiscoveryField("mean_spo2", "Mean SpO2", "%", "measurement"),
                new DiscoveryField("min_spo2", "Min SpO2", "%", "measurement"),
                new DiscoveryField("mean_hr_bpm", "Mean HR", "bpm", "measurement"),
                new DiscoveryField("max_hr_bpm", "Max HR", "bpm", "measurement"),
                new DiscoveryField("duration_minutes", "Duration", "min", "measurement"),
                new DiscoveryField("packet_count", "Packet Count", "packets", "measurement"),
                new DiscoveryField("apnea_events", "Apnea Events", "events", "total"),
                new DiscoveryField("central_events", "Central Events", "events", "total")
        );
    }

    public static String normalizeServerUri(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) {
            return "";
        }
        if (!value.contains("://")) {
            if (!value.contains(":")) {
                value = value + ":1883";
            }
            value = "tcp://" + value;
        }
        return value;
    }

    public static void publishSummary(
            String serverUri,
            String username,
            String password,
            String payload
    ) throws Exception {
        String normalized = normalizeServerUri(serverUri);
        if (normalized.isEmpty()) {
            throw new MqttException(new IllegalArgumentException("MQTT server URI is empty"));
        }

        String clientId = String.format(Locale.US, "watchpat-android-%s", UUID.randomUUID());
        MqttClient client = new MqttClient(normalized, clientId, new MemoryPersistence());
        MqttConnectOptions options = new MqttConnectOptions();
        options.setAutomaticReconnect(false);
        options.setCleanSession(true);
        options.setConnectionTimeout(10);
        if (username != null && !username.trim().isEmpty()) {
            options.setUserName(username.trim());
        }
        if (password != null && !password.isEmpty()) {
            options.setPassword(password.toCharArray());
        }

        try {
            client.connect(options);
            publishDiscovery(client);
            MqttMessage message = new MqttMessage(payload.getBytes(StandardCharsets.UTF_8));
            message.setQos(1);
            message.setRetained(true);
            client.publish(DEFAULT_TOPIC, message);
        } finally {
            if (client.isConnected()) {
                client.disconnect();
            }
            client.close();
        }
    }

    private static void publishDiscovery(MqttClient client) throws Exception {
        JSONObject device = new JSONObject();
        device.put("identifiers", new org.json.JSONArray().put(DEVICE_ID));
        device.put("name", "WatchPAT Android Summary");
        device.put("manufacturer", "WatchPAT");
        device.put("model", "Android Recorder");

        for (DiscoveryField field : discoveryFields()) {
            JSONObject payload = new JSONObject();
            payload.put("name", "WatchPAT " + field.label);
            payload.put("unique_id", DEVICE_ID + "_" + field.key);
            payload.put("state_topic", DEFAULT_TOPIC);
            payload.put("value_template", "{{ value_json." + field.key + " }}");
            payload.put("device", device);
            if (field.unit != null && !field.unit.isEmpty()) {
                payload.put("unit_of_measurement", field.unit);
            }
            if (field.stateClass != null && !field.stateClass.isEmpty()) {
                payload.put("state_class", field.stateClass);
            }
            String topic = DISCOVERY_PREFIX + "/sensor/" + DEVICE_ID + "/" + field.key + "/config";
            MqttMessage message = new MqttMessage(payload.toString().getBytes(StandardCharsets.UTF_8));
            message.setQos(1);
            message.setRetained(true);
            client.publish(topic, message);
        }
    }
}
