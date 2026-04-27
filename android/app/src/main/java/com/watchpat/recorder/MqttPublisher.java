package com.watchpat.recorder;

import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.UUID;

public final class MqttPublisher {

    public static final String DEFAULT_TOPIC = "watchpat/analysis";

    private MqttPublisher() {}

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
    ) throws MqttException {
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
            MqttMessage message = new MqttMessage(payload.getBytes(StandardCharsets.UTF_8));
            message.setQos(1);
            message.setRetained(false);
            client.publish(DEFAULT_TOPIC, message);
        } finally {
            if (client.isConnected()) {
                client.disconnect();
            }
            client.close();
        }
    }
}
