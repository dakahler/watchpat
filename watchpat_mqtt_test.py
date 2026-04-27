#!/usr/bin/env python3
"""
Publish a WatchPAT analysis summary to MQTT and verify the broker echoes it back.

This is a real integration check against a live broker, not a unit test.

Examples:
  python watchpat_mqtt_test.py --server 192.168.1.50
  python watchpat_mqtt_test.py --server tcp://broker.example.com:1883 --username user --password secret
"""

import argparse
import json
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import paho.mqtt.client as mqtt

from watchpat_diff import RecordingSummary, summarize_dat_file


DEFAULT_TOPIC = "watchpat/analysis/test"
DISCOVERY_PREFIX = "homeassistant"
DISCOVERY_DEVICE_ID = "watchpat_test_summary"


DISCOVERY_FIELDS = [
    ("ahi", "AHI", "/hr", "measurement"),
    ("pahi", "pAHI", "/hr", "measurement"),
    ("prdi", "pRDI", "/hr", "measurement"),
    ("awake_pct", "Awake", "%", "measurement"),
    ("light_pct", "Light", "%", "measurement"),
    ("deep_pct", "Deep", "%", "measurement"),
    ("rem_pct", "REM", "%", "measurement"),
    ("mean_spo2", "Mean SpO2", "%", "measurement"),
    ("min_spo2", "Min SpO2", "%", "measurement"),
    ("mean_hr_bpm", "Mean HR", "bpm", "measurement"),
    ("max_hr_bpm", "Max HR", "bpm", "measurement"),
    ("duration_minutes", "Duration", "min", "measurement"),
    ("packet_count", "Packet Count", "packets", "measurement"),
    ("apnea_events", "Apnea Events", "events", "total"),
    ("central_events", "Central Events", "events", "total"),
]
DEFAULT_INPUT = Path("tests/testdata2.dat")


def normalize_server_uri(raw: str) -> tuple[str, int]:
    text = raw.strip()
    if not text:
        raise ValueError("MQTT server is required")
    if "://" in text:
        scheme, rest = text.split("://", 1)
        if scheme.lower() != "tcp":
            raise ValueError("Only tcp:// MQTT endpoints are supported")
        text = rest
    if ":" in text:
        host, port_text = text.rsplit(":", 1)
        return host, int(port_text)
    return text, 1883


def build_summary_payload(summary: RecordingSummary) -> dict:
    stage_percentages = summary.sleep_stage_percentages
    return {
        "recording_path": summary.path,
        "packet_count": summary.packet_count,
        "duration_minutes": round(summary.duration_seconds / 60.0, 2),
        "ahi": summary.ahi_estimate,
        "pahi": summary.pahi_estimate,
        "prdi": summary.rdi_estimate,
        "apnea_events": summary.apnea_events,
        "central_events": summary.central_events,
        "pat_event_counts": summary.pat_event_counts,
        "mean_hr_bpm": summary.hr_mean,
        "max_hr_bpm": summary.hr_max,
        "mean_spo2": summary.spo2_mean,
        "min_spo2": summary.spo2_min,
        "metric_mean": summary.metric_mean,
        "body_positions": summary.body_positions,
        "sleep_stage_percentages": stage_percentages,
        "awake_pct": stage_percentages.get("Awake", 0.0),
        "light_pct": stage_percentages.get("Light", 0.0),
        "deep_pct": stage_percentages.get("Deep", 0.0),
        "rem_pct": stage_percentages.get("REM", 0.0),
    }


def build_discovery_messages(state_topic: str, device_id: str = DISCOVERY_DEVICE_ID) -> list[tuple[str, str]]:
    messages = []
    device = {
        "identifiers": [device_id],
        "name": "WatchPAT Test Summary",
        "manufacturer": "WatchPAT",
        "model": "Summary Export",
    }
    for field, label, unit, state_class in DISCOVERY_FIELDS:
        topic = f"{DISCOVERY_PREFIX}/sensor/{device_id}/{field}/config"
        payload = {
            "name": f"WatchPAT Test {label}",
            "unique_id": f"{device_id}_{field}",
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json.{field} }}}}",
            "device": device,
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if state_class:
            payload["state_class"] = state_class
        if field in {
            "ahi", "pahi", "prdi",
            "awake_pct", "light_pct", "deep_pct", "rem_pct",
            "mean_spo2", "min_spo2", "mean_hr_bpm", "max_hr_bpm",
            "duration_minutes",
        }:
            payload["suggested_display_precision"] = 2
        messages.append((topic, json.dumps(payload, sort_keys=True)))
    return messages


def verify_publish(
    host: str,
    port: int,
    topic: str,
    payload: dict,
    username: str = "",
    password: str = "",
    retain: bool = False,
    timeout_s: float = 10.0,
) -> None:
    expected_json = json.dumps(payload, sort_keys=True)
    discovery_messages = build_discovery_messages(topic)
    received = {"payload": None, "error": None}
    ready = threading.Event()
    delivered = threading.Event()

    def on_connect_sub(client, _userdata, _flags, rc):
        if rc != 0:
            received["error"] = f"subscriber connect failed rc={rc}"
            ready.set()
            delivered.set()
            return
        client.subscribe(topic, qos=1)
        ready.set()

    def on_message(_client, _userdata, msg):
        received["payload"] = msg.payload.decode("utf-8")
        delivered.set()

    subscriber = mqtt.Client(client_id=f"watchpat-sub-{uuid.uuid4().hex}")
    publisher = mqtt.Client(client_id=f"watchpat-pub-{uuid.uuid4().hex}")
    if username:
        subscriber.username_pw_set(username, password or None)
        publisher.username_pw_set(username, password or None)

    subscriber.on_connect = on_connect_sub
    subscriber.on_message = on_message

    subscriber.connect(host, port, keepalive=30)
    subscriber.loop_start()
    try:
        if not ready.wait(timeout_s):
            raise TimeoutError("timed out waiting for subscriber to connect")
        if received["error"]:
            raise RuntimeError(received["error"])

        publisher.connect(host, port, keepalive=30)
        publisher.loop_start()
        try:
            for discovery_topic, discovery_payload in discovery_messages:
                info = publisher.publish(
                    discovery_topic, payload=discovery_payload, qos=1, retain=True
                )
                info.wait_for_publish(timeout=timeout_s)
                if not info.is_published():
                    raise TimeoutError(
                        f"discovery publish did not complete for topic {discovery_topic}"
                    )
            info = publisher.publish(topic, payload=expected_json, qos=1, retain=retain)
            info.wait_for_publish(timeout=timeout_s)
            if not info.is_published():
                raise TimeoutError("publish did not complete before timeout")
        finally:
            try:
                publisher.disconnect()
            finally:
                publisher.loop_stop()

        if not delivered.wait(timeout_s):
            raise TimeoutError("timed out waiting for broker to deliver published summary")
        if received["payload"] != expected_json:
            raise AssertionError(
                "broker delivered unexpected payload\n"
                f"expected: {expected_json}\n"
                f"received: {received['payload']}"
            )
    finally:
        subscriber.loop_stop()
        try:
            subscriber.disconnect()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dat",
        type=Path,
        default=DEFAULT_INPUT,
        help="Capture to analyze before publishing",
    )
    parser.add_argument(
        "--server",
        required=True,
        help="MQTT broker host, host:port, or tcp://host:port",
    )
    parser.add_argument("--username", default="", help="Optional MQTT username")
    parser.add_argument("--password", default="", help="Optional MQTT password")
    parser.add_argument(
        "--topic",
        default=DEFAULT_TOPIC,
        help="MQTT topic to use",
    )
    parser.add_argument(
        "--retain",
        action="store_true",
        help="Publish the summary as a retained MQTT message",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Timeout in seconds for broker operations",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input_dat.is_file():
        raise FileNotFoundError(f"Input .dat not found: {args.input_dat}")

    summary = summarize_dat_file(str(args.input_dat))
    payload = build_summary_payload(summary)
    host, port = normalize_server_uri(args.server)
    topic = args.topic

    started = time.time()
    verify_publish(
        host=host,
        port=port,
        topic=topic,
        payload=payload,
        username=args.username,
        password=args.password,
        retain=args.retain,
        timeout_s=args.timeout,
    )
    elapsed = time.time() - started

    print("MQTT verification successful")
    print(f"Broker: {host}:{port}")
    print(f"Topic:  {topic}")
    print(f"Discovery prefix: {DISCOVERY_PREFIX}")
    print(f"Time:   {elapsed:.2f}s")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
