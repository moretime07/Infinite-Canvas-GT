import argparse
import os
import signal
import socket
import subprocess
import time
from pathlib import Path


def wait_for_port_release(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.25)
    return False


def stop_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


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
    time.sleep(max(0.5, args.delay))
    stop_process(args.pid)
    if not wait_for_port_release(args.port):
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"Timed out waiting for port {args.port} to be released.\n")
        return 1
    start_backend(args.python, app_dir, log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
