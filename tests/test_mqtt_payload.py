"""Unit tests for the MQTT summary payload helper."""

import argparse
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from watchpat_mqtt_test import (
    DEFAULT_TOPIC,
    build_discovery_messages,
    build_summary_payload,
    normalize_server_uri,
    parse_args,
)
from watchpat_diff import summarize_dat_file


TESTDATA = os.path.join(os.path.dirname(__file__), "testdata.dat")


class TestMqttPayloadHelpers(unittest.TestCase):
    def test_normalize_server_uri_accepts_common_forms(self):
        self.assertEqual(normalize_server_uri("broker.example.com"), ("broker.example.com", 1883))
        self.assertEqual(normalize_server_uri("broker.example.com:1884"), ("broker.example.com", 1884))
        self.assertEqual(normalize_server_uri("tcp://10.0.0.5:1883"), ("10.0.0.5", 1883))

    def test_build_summary_payload_contains_expected_metrics(self):
        summary = summarize_dat_file(TESTDATA)
        payload = build_summary_payload(summary)

        self.assertIn("ahi", payload)
        self.assertIn("pahi", payload)
        self.assertIn("prdi", payload)
        self.assertIn("mean_spo2", payload)
        self.assertIn("mean_hr_bpm", payload)
        self.assertIn("sleep_stage_percentages", payload)
        self.assertIn("awake_pct", payload)
        self.assertIn("rem_pct", payload)
        self.assertEqual(payload["packet_count"], 15)

    def test_parse_args_defaults_to_primary_topic_without_retain(self):
        argv = sys.argv
        try:
            sys.argv = ["watchpat_mqtt_test.py", "--server", "broker.example.com"]
            args = parse_args()
        finally:
            sys.argv = argv
        self.assertEqual(args.topic, DEFAULT_TOPIC)
        self.assertFalse(args.retain)

    def test_discovery_messages_use_test_state_topic(self):
        messages = build_discovery_messages("watchpat/analysis/test")
        self.assertGreater(len(messages), 3)
        topic, payload = messages[0]
        self.assertTrue(topic.startswith("homeassistant/sensor/watchpat_test_summary/"))
        self.assertIn('"state_topic": "watchpat/analysis/test"', payload)
        rendered = "\n".join(body for _, body in messages)
        self.assertIn('"unique_id": "watchpat_test_summary_awake_pct"', rendered)


if __name__ == "__main__":
    unittest.main()
