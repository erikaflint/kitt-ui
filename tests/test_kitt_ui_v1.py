import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitt_ui_import import load_server_module


server = load_server_module()


class KittUIV1Tests(unittest.TestCase):
    def test_job_defaults_are_safe_for_yellow(self):
        defaults = server.job_defaults("yellow")
        self.assertEqual(defaults["risk_level"], "yellow")
        self.assertTrue(defaults["audit_required"])
        self.assertFalse(defaults["approval_required"])
        self.assertFalse(defaults["payload"]["implementation_allowed"])
        self.assertEqual(defaults["payload"]["scope_status"], "unscoped")

    def test_job_defaults_require_approval_for_red(self):
        defaults = server.job_defaults("red")
        self.assertTrue(defaults["audit_required"])
        self.assertTrue(defaults["approval_required"])

    def test_build_job_payload_rejects_missing_title(self):
        with self.assertRaises(ValueError):
            server.build_job_payload({"service": "kitt-ui"})

    def test_build_job_payload_is_queued_safe_shape(self):
        payload = server.build_job_payload(
            {
                "title": "  [TEST] KITT UI smoke  ",
                "service": "kitt-ui-test",
                "owner": "role:test",
                "priority": "urgent",
                "risk_level": "yellow",
                "instructions": "Create a safe test job.",
                "definition_of_done": "The job appears in the runtime list.",
            }
        )
        self.assertEqual(payload["title"], "[TEST] KITT UI smoke")
        self.assertEqual(payload["service"], "kitt-ui-test")
        self.assertEqual(payload["priority"], "urgent")
        self.assertEqual(payload["risk_level"], "yellow")
        self.assertTrue(payload["audit_required"])
        self.assertFalse(payload["approval_required"])
        self.assertEqual(payload["source"], "kitt-ui-v1")
        self.assertFalse(payload["payload"]["implementation_allowed"])
        json.dumps(payload)

    def test_load_packets_includes_calendar_intelligence_sample(self):
        packets = server.load_packets()
        calendar_packets = [
            packet for packet in packets if packet.get("packet_type") == "calendar_intelligence"
        ]
        self.assertTrue(calendar_packets)
        packet = calendar_packets[0]
        self.assertEqual(packet["source"], "acuity")
        self.assertEqual(packet["campaign"], "fill_my_calendar")
        self.assertEqual(packet["_mode"], "sample")


if __name__ == "__main__":
    unittest.main()
