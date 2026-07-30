import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def request_from(host):
    return SimpleNamespace(client=SimpleNamespace(host=host))


RESTART_HEADERS = {
    "host": "127.0.0.1:3000",
    "origin": "http://127.0.0.1:3000",
    "sec-fetch-site": "same-origin",
    "x-infinite-canvas-restart": "1",
}


def restart_request(headers=None, client=("127.0.0.1", 50000)):
    async def request():
        transport = httpx.ASGITransport(app=main.app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:3000") as api:
            return await api.post("/api/system/restart", headers=headers or RESTART_HEADERS)

    return asyncio.run(request())


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

    def test_restart_accepts_same_origin_loopback_request_and_schedules_once(self):
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            first = restart_request()
            second = restart_request()

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["ok"])
        self.assertEqual(first.json()["instance_id"], main.APP_INSTANCE_ID)
        self.assertTrue(second.json()["already_scheduled"])
        schedule.assert_called_once()

    def test_restart_accepts_ipv4_mapped_loopback_host_and_origin(self):
        headers = {
            **RESTART_HEADERS,
            "host": "[::ffff:127.0.0.1]:3000",
            "origin": "http://[::ffff:127.0.0.1]:3000",
        }
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            response = restart_request(headers, client=("::ffff:127.0.0.1", 50000))

        self.assertEqual(response.status_code, 200)
        schedule.assert_called_once()

    def test_restart_rejects_missing_restart_header_without_scheduling(self):
        headers = {key: value for key, value in RESTART_HEADERS.items() if key != "x-infinite-canvas-restart"}
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            response = restart_request(headers)

        self.assertEqual(response.status_code, 403)
        schedule.assert_not_called()

    def test_restart_rejects_foreign_origin_without_scheduling(self):
        headers = {**RESTART_HEADERS, "origin": "https://attacker.example"}
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            response = restart_request(headers)

        self.assertEqual(response.status_code, 403)
        schedule.assert_not_called()

    def test_restart_rejects_dns_rebinding_host_without_scheduling(self):
        headers = {**RESTART_HEADERS, "host": "attacker.example:3000", "origin": "http://attacker.example:3000"}
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            response = restart_request(headers)

        self.assertEqual(response.status_code, 403)
        schedule.assert_not_called()

    def test_restart_rejects_null_origin_without_scheduling(self):
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            response = restart_request({**RESTART_HEADERS, "origin": "null"})

        self.assertEqual(response.status_code, 403)
        schedule.assert_not_called()

    def test_restart_rejects_non_loopback_client_without_scheduling(self):
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            response = restart_request(client=("192.168.2.88", 50000))

        self.assertEqual(response.status_code, 403)
        schedule.assert_not_called()

    def test_restart_rejects_mismatched_loopback_port_without_scheduling(self):
        headers = {**RESTART_HEADERS, "origin": "http://127.0.0.1:8080"}
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            response = restart_request(headers)

        self.assertEqual(response.status_code, 403)
        schedule.assert_not_called()

    def test_restart_rejects_cross_site_fetch_metadata_without_scheduling(self):
        headers = {**RESTART_HEADERS, "sec-fetch-site": "cross-site"}
        with patch.object(main, "schedule_backend_restart", return_value=True) as schedule:
            response = restart_request(headers)

        self.assertEqual(response.status_code, 403)
        schedule.assert_not_called()

    def test_restart_spawn_failure_keeps_request_retryable(self):
        with patch.object(main, "schedule_backend_restart", return_value=False):
            response = restart_request()
        self.assertEqual(response.status_code, 500)
        self.assertFalse(main.BACKEND_RESTART_SCHEDULED)

    def test_scheduler_uses_fixed_local_restart_command(self):
        with patch.object(main.subprocess, "Popen") as popen, patch.object(main.os, "getpid", return_value=4321):
            self.assertTrue(main.schedule_backend_restart())

        command = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        self.assertEqual(command[:2], [sys.executable, os.path.join(main.BASE_DIR, "tools", "restart_backend.py")])
        self.assertEqual(command[command.index("--pid") + 1], "4321")
        self.assertEqual(command[command.index("--python") + 1], sys.executable)
        self.assertEqual(command[command.index("--app-dir") + 1], main.BASE_DIR)
        self.assertEqual(command[command.index("--port") + 1], "3000")
        self.assertEqual(kwargs["cwd"], main.BASE_DIR)
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & getattr(main.subprocess, "DETACHED_PROCESS", 0))
            self.assertTrue(kwargs["creationflags"] & getattr(main.subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    unittest.main()
