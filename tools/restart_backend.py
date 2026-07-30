import argparse
import ctypes
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path


PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_bool
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel32


def open_process_handle(pid: int):
    if os.name != "nt":
        return None
    try:
        return _kernel32().OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid) or None
    except OSError:
        return None


def close_process_handle(handle) -> None:
    if os.name == "nt" and handle:
        try:
            _kernel32().CloseHandle(handle)
        except OSError:
            pass


def _sanitize_diagnostic(message: str) -> str:
    value = " ".join(str(message or "").split())[:500]
    value = re.sub(r"(?i)(api[_ -]?key|token|secret|password)\s*[=:]\s*\S+", r"\1=[redacted]", value)
    return re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", value)


def append_diagnostic(log_path: Path, message: str) -> None:
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"{_sanitize_diagnostic(message)}\\n")
    except OSError:
        pass


def wait_for_port_release(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.25)
    return False


def stop_process(pid: int, process_handle=None) -> bool:
    if os.name == "nt":
        if not process_handle:
            return False
        try:
            kernel32 = _kernel32()
            if not kernel32.TerminateProcess(process_handle, 1):
                return False
            return kernel32.WaitForSingleObject(process_handle, 5000) == WAIT_OBJECT_0
        except OSError:
            return False
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return True


def start_backend(python_executable: str, app_dir: Path, log_path: Path) -> None:
    kwargs = {"cwd": str(app_dir), "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen(
            [python_executable, str(app_dir / "main.py")],
            stdout=log,
            stderr=subprocess.STDOUT,
            **kwargs,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    log_path = Path(args.log).resolve()
    process_handle = open_process_handle(args.pid) if os.name == "nt" else None
    try:
        if os.name == "nt" and not process_handle:
            append_diagnostic(log_path, "Unable to acquire a handle for the old backend process.")
            return 1
        time.sleep(max(0.5, args.delay))
        if not stop_process(args.pid, process_handle):
            append_diagnostic(log_path, "Unable to stop the old backend process.")
            return 1
        if not wait_for_port_release(args.port):
            append_diagnostic(log_path, f"Timed out waiting for port {args.port} to be released.")
            return 1
        start_backend(args.python, app_dir, log_path)
        return 0
    except Exception as exc:
        append_diagnostic(log_path, f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        if process_handle:
            close_process_handle(process_handle)


if __name__ == "__main__":
    raise SystemExit(main())
