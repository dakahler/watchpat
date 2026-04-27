package com.watchpat.recorder;

import android.content.SharedPreferences;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

public class MqttConfigActivity extends AppCompatActivity {

    public static final String PREFS_NAME = "mqtt_config";
    public static final String KEY_SERVER_URI = "server_uri";
    public static final String KEY_USERNAME = "username";
    public static final String KEY_PASSWORD = "password";

    private EditText etServer;
    private EditText etUsername;
    private EditText etPassword;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_mqtt_config);

        etServer = findViewById(R.id.et_mqtt_server);
        etUsername = findViewById(R.id.et_mqtt_username);
        etPassword = findViewById(R.id.et_mqtt_password);
        Button btnSave = findViewById(R.id.btn_save_mqtt);

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        etServer.setText(prefs.getString(KEY_SERVER_URI, ""));
        etUsername.setText(prefs.getString(KEY_USERNAME, ""));
        etPassword.setText(prefs.getString(KEY_PASSWORD, ""));

        btnSave.setOnClickListener(v -> {
            String serverUri = etServer.getText().toString().trim();
            String username = etUsername.getText().toString().trim();
            String password = etPassword.getText().toString();
            prefs.edit()
                    .putString(KEY_SERVER_URI, serverUri)
                    .putString(KEY_USERNAME, username)
                    .putString(KEY_PASSWORD, password)
                    .apply();
            Toast.makeText(this, R.string.mqtt_saved, Toast.LENGTH_SHORT).show();
            finish();
        });
    }
}
