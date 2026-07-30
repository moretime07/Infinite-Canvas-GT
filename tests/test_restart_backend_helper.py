import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


HELPER_PATH = Path(__file__).resolve().parents[1] / "tools" / "restart_backend.py"
SPEC = importlib.util.spec_from_file_location("restart_backend_helper", HELPER_PATH)
restart_backend = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = restart_backend
SPEC.loader.exec_module(restart_backend)


class RestartBackendHelperTests(unittest.TestCase):
    def helper_args(self, log_path):
        return [
            "restart_backend.py", "--pid", "1234", "--python", sys.executable,
            "--app-dir", str(HELPER_PATH.parents[1]), "--port", "3000",
            "--delay", "0.5", "--log", str(log_path),
        ]

    def test_port_release_timeout_returns_nonzero_and_logs_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "restart.log"
            with patch.object(sys, "argv", self.helper_args(log_path)), \
                 patch.object(restart_backend, "open_process_handle", return_value=object()), \
                 patch.object(restart_backend, "close_process_handle"), \
                 patch.object(restart_backend.time, "sleep"), \
                 patch.object(restart_backend, "stop_process"), \
                 patch.object(restart_backend, "wait_for_port_release", return_value=False):
                result = restart_backend.main()

            self.assertEqual(result, 1)
            self.assertIn("Timed out waiting for port 3000", log_path.read_text(encoding="utf-8"))

    def test_backend_spawn_failure_returns_nonzero_and_logs_sanitized_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "restart.log"
            with patch.object(sys, "argv", self.helper_args(log_path)), \
                 patch.object(restart_backend, "open_process_handle", return_value=object()), \
                 patch.object(restart_backend, "close_process_handle"), \
                 patch.object(restart_backend.time, "sleep"), \
                 patch.object(restart_backend, "stop_process"), \
                 patch.object(restart_backend, "wait_for_port_release", return_value=True), \
                 patch.object(restart_backend, "start_backend", side_effect=RuntimeError("spawn unavailable")):
                result = restart_backend.main()

            self.assertEqual(result, 1)
            log = log_path.read_text(encoding="utf-8")
            self.assertIn("RuntimeError: spawn unavailable", log)

    @unittest.skipUnless(restart_backend.os.name == "nt", "Windows process handles are Windows-only")
    def test_windows_pins_old_process_handle_before_delay_and_closes_it(self):
        events = []
        handle = object()
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "restart.log"
            with patch.object(sys, "argv", self.helper_args(log_path)), \
                 patch.object(restart_backend, "open_process_handle", side_effect=lambda pid: events.append("open") or handle), \
                 patch.object(restart_backend.time, "sleep", side_effect=lambda delay: events.append("sleep")), \
                 patch.object(restart_backend, "stop_process", side_effect=lambda pid, old_handle: events.append("stop") or True), \
                 patch.object(restart_backend, "wait_for_port_release", return_value=False), \
                 patch.object(restart_backend, "close_process_handle", side_effect=lambda old_handle: events.append("close")):
                result = restart_backend.main()

        self.assertEqual(result, 1)
        self.assertEqual(events, ["open", "sleep", "stop", "close"])

    def test_diagnostic_redacts_secret_values(self):
        self.assertEqual(
            restart_backend._sanitize_diagnostic("RuntimeError: api_key=super-secret-token"),
            "RuntimeError: api_key=[redacted]",
        )

    @unittest.skipUnless(restart_backend.os.name == "nt", "Windows process handles are Windows-only")
    def test_windows_stop_process_rejects_a_handle_wait_timeout(self):
        kernel32 = SimpleNamespace(
            TerminateProcess=lambda handle, code: True,
            WaitForSingleObject=lambda handle, timeout: 258,
        )
        with patch.object(restart_backend, "_kernel32", return_value=kernel32):
            self.assertFalse(restart_backend.stop_process(1234, object()))


if __name__ == "__main__":
    unittest.main()
