import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def request_from(host):
    return SimpleNamespace(client=SimpleNamespace(host=host))


class BackendRestartTests(unittest.TestCase):
    def setUp(self):
        main.BACKEND_RESTART_SCHEDULED = False

    def tearDown(self):
        main.BACKEND_RESTART_SCHEDULED = False

    def test_loopback_detection_accepts_ipv4_ipv6_and_mapped_ipv4(self):
        self.assertTrue(main.is_loopback_client(request_from("127.0.0.1")))
        self.assertTrue(main.is_loopback_client(request_from("::1")))
        self.assertTrue(main.is_loopback_client(request_from("::ffff:127.0.0.1")))

    def test_loopback_detection_rejects_lan_and_missing_clients(self):
        self.assertFalse(main.is_loopback_client(request_from("192.168.2.88")))
        self.assertFalse(main.is_loopback_client(SimpleNamespace(client=None)))

    def test_status_exposes_instance_id_and_local_capability(self):
        result = main.system_status(request_from("127.0.0.1"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["instance_id"], main.APP_INSTANCE_ID)
        self.assertTrue(result["can_restart"])

    def test_restart_rejects_non_loopback_request(self):
        with self.assertRaises(HTTPException) as raised:
            main.restart_backend(request_from("192.168.2.88"))
        self.assertEqual(raised.exception.status_code, 403)

    def test_restart_schedules_once_and_returns_old_instance_id(self):
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            first = main.restart_backend(request_from("127.0.0.1"))
            second = main.restart_backend(request_from("127.0.0.1"))

        self.assertTrue(first["ok"])
        self.assertEqual(first["instance_id"], main.APP_INSTANCE_ID)
        self.assertTrue(second["already_scheduled"])
        schedule.assert_called_once()

    def test_restart_spawn_failure_keeps_request_retryable(self):
        with patch.object(main, "schedule_backend_restart", return_value=False):
            with self.assertRaises(HTTPException) as raised:
                main.restart_backend(request_from("127.0.0.1"))
        self.assertEqual(raised.exception.status_code, 500)
        self.assertFalse(main.BACKEND_RESTART_SCHEDULED)


if __name__ == "__main__":
    unittest.main()
