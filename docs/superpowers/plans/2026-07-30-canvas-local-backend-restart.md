# Canvas Local Backend Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a localhost-only “重启后台” action to the blank-canvas context menu that silently restarts only the Infinite Canvas backend and reloads the page after a new backend instance is available.

**Architecture:** FastAPI exposes a lightweight instance-status route and a loopback-only restart route. The restart route launches a detached helper process with the current Python interpreter; the helper stops the old PID, waits for port 3000 to be released, and starts `main.py` without opening another browser. The canvas UI confirms the destructive action, polls for a changed instance ID, and reloads only after the replacement backend is online.

**Tech Stack:** Python 3, FastAPI, Windows process APIs through `subprocess`, vanilla JavaScript, HTML, Node.js built-in assertions, Python `unittest`.

## Global Constraints

- Restart only the Infinite Canvas backend process that serves port 3000.
- Do not restart ComfyUI, RunningHub, or any external service.
- Show and allow the action only for loopback clients (`localhost`, `127.0.0.1`, `::1`, or an IPv4-mapped loopback address).
- Enforce loopback authorization in the backend; frontend visibility is not a security boundary.
- Do not accept a PID, command, executable path, working directory, or port from the HTTP request.
- Launch the replacement with the current Python interpreter and current project directory.
- Do not open an extra terminal window or browser window.
- Warn that active tasks and LAN users will be interrupted.
- Poll every 800 ms for at most 45 seconds and reload only after `instance_id` changes.
- Do not log API keys, request bodies, or private provider configuration.
- Preserve all unrelated existing worktree changes.

---

### Task 1: Add the loopback-only backend restart service

**Files:**
- Create: `tools/restart_backend.py`
- Create: `tests/test_backend_restart.py`
- Modify: `main.py` near the application globals, `/api/app-info`, and existing restart utilities

**Interfaces:**
- Consumes: `BASE_DIR`, `current_app_version()`, `Request`, `HTTPException`, `sys.executable`, and the existing FastAPI `app`.
- Produces: `APP_INSTANCE_ID: str`, `is_loopback_client(request: Request) -> bool`, `schedule_backend_restart(delay_seconds: float = 1.5) -> bool`, `GET /api/system/status`, and `POST /api/system/restart`.
- Helper CLI: `python tools/restart_backend.py --pid <old_pid> --python <python_executable> --app-dir <project_dir> --port 3000 --delay 1.5 --log <log_path>`.

- [ ] **Step 1: Write failing backend authorization and scheduling tests**

Create `tests/test_backend_restart.py` with tests that call the route functions directly so the test runner is never terminated:

```python
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
```

- [ ] **Step 2: Run the backend tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_backend_restart -v
```

Expected: failures report missing `is_loopback_client`, `system_status`, `restart_backend`, or restart state symbols.

- [ ] **Step 3: Implement the detached restart helper**

Create `tools/restart_backend.py` with a fixed-purpose CLI. Its core behavior must be equivalent to:

```python
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
    flags = 0
    kwargs = {"cwd": str(app_dir), "close_fds": True}
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        kwargs["creationflags"] = flags
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
            log.write("Timed out waiting for port 3000 to be released.\n")
        return 1
    start_backend(args.python, app_dir, log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Keep every command argument separate; do not build a shell command string.

- [ ] **Step 4: Implement backend state, authorization, scheduling, and routes**

Add startup-scoped state near the FastAPI application globals:

```python
APP_INSTANCE_ID = uuid.uuid4().hex
BACKEND_RESTART_LOCK = Lock()
BACKEND_RESTART_SCHEDULED = False
```

Add the following behavior in `main.py`:

```python
def is_loopback_client(request: Request) -> bool:
    host = str(getattr(getattr(request, "client", None), "host", "") or "").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def schedule_backend_restart(delay_seconds: float = 1.5) -> bool:
    helper = os.path.join(BASE_DIR, "tools", "restart_backend.py")
    log_path = os.path.join(BASE_DIR, "_backend_restart.log")
    command = [
        sys.executable,
        helper,
        "--pid", str(os.getpid()),
        "--python", sys.executable,
        "--app-dir", BASE_DIR,
        "--port", "3000",
        "--delay", str(max(0.5, float(delay_seconds))),
        "--log", log_path,
    ]
    kwargs = {"cwd": BASE_DIR, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    try:
        subprocess.Popen(command, **kwargs)
        return True
    except Exception:
        logging.exception("Unable to schedule backend restart")
        return False


@app.get("/api/system/status")
def system_status(request: Request):
    return {
        "ok": True,
        "instance_id": APP_INSTANCE_ID,
        "version": current_app_version(),
        "can_restart": is_loopback_client(request),
    }


@app.post("/api/system/restart")
def restart_backend(request: Request):
    global BACKEND_RESTART_SCHEDULED
    if not is_loopback_client(request):
        raise HTTPException(status_code=403, detail="仅允许在服务器本机重启后台")
    with BACKEND_RESTART_LOCK:
        if BACKEND_RESTART_SCHEDULED:
            return {
                "ok": True,
                "scheduled": True,
                "already_scheduled": True,
                "instance_id": APP_INSTANCE_ID,
            }
        if not schedule_backend_restart():
            raise HTTPException(status_code=500, detail="无法创建后台重启进程，当前服务仍在运行")
        BACKEND_RESTART_SCHEDULED = True
    return {
        "ok": True,
        "scheduled": True,
        "already_scheduled": False,
        "instance_id": APP_INSTANCE_ID,
    }
```

- [ ] **Step 5: Run backend tests and syntax validation**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_backend_restart -v
.\.venv\Scripts\python.exe -m py_compile main.py tools\restart_backend.py
```

Expected: all restart tests pass and both Python files compile.

- [ ] **Step 6: Commit the backend unit**

```powershell
git add main.py tools/restart_backend.py tests/test_backend_restart.py
git commit -m "feat: add localhost backend restart service"
```

---

### Task 2: Add the blank-canvas restart action and recovery polling

**Files:**
- Create: `tests/canvas-backend-restart.test.js`
- Modify: `static/canvas.html` inside `#createMenu`
- Modify: `static/js/canvas.js` near menu state, `openCreateMenu`, and status helpers
- Modify: `static/js/i18n/canvas.js`

**Interfaces:**
- Consumes: `POST /api/system/restart`, `GET /api/system/status`, `closeCreateMenu()`, `setStatus()`, `showErrorModal()`, `responseErrorMessage()`, `tr()`, and `refreshIcons()`.
- Produces: `isLocalRestartHost(hostname: string) -> boolean`, `syncBackendRestartMenuVisibility()`, `waitForBackendInstance(oldInstanceId: string, options?: {timeoutMs?: number, intervalMs?: number}) -> Promise<object>`, and `restartCanvasBackend() -> Promise<void>`.

- [ ] **Step 1: Write failing frontend behavior tests**

Create `tests/canvas-backend-restart.test.js`:

```javascript
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const jsSource = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'js', 'canvas.js'), 'utf8');
const htmlSource = fs.readFileSync(path.resolve(__dirname, '..', 'static', 'canvas.html'), 'utf8');

function productionFunction(name){
    const asyncStart = jsSource.indexOf(`async function ${name}(`);
    const syncStart = jsSource.indexOf(`function ${name}(`);
    const start = asyncStart === -1 ? syncStart : asyncStart;
    assert.notEqual(start, -1, `canvas.js should define ${name}`);
    const next = jsSource.slice(start + 1).search(/\n(?:async )?function /);
    return jsSource.slice(start, next === -1 ? jsSource.length : start + 1 + next);
}

assert.match(htmlSource, /id="backendRestartMenuSection"[^>]*hidden/);
assert.match(htmlSource, /id="backendRestartMenuBtn"/);
assert.match(htmlSource, /onclick="restartCanvasBackend\(\)"/);

const hostContext = {};
vm.createContext(hostContext);
vm.runInContext(
    `${productionFunction('isLocalRestartHost')}; this.isLocalRestartHost = isLocalRestartHost;`,
    hostContext
);

assert.equal(hostContext.isLocalRestartHost('localhost'), true);
assert.equal(hostContext.isLocalRestartHost('127.0.0.1'), true);
assert.equal(hostContext.isLocalRestartHost('::1'), true);
assert.equal(hostContext.isLocalRestartHost('[::1]'), true);
assert.equal(hostContext.isLocalRestartHost('192.168.2.88'), false);

let statusCalls = 0;
const pollingContext = {
    fetch: async () => {
        statusCalls += 1;
        const instance_id = statusCalls === 1 ? 'old-instance' : 'new-instance';
        return {ok:true, json:async () => ({ok:true, instance_id})};
    },
    setTimeout: callback => callback(),
    Date,
    Error,
};
vm.createContext(pollingContext);
vm.runInContext(
    `${productionFunction('waitForBackendInstance')}; this.waitForBackendInstance = waitForBackendInstance;`,
    pollingContext
);

(async () => {
    const result = await pollingContext.waitForBackendInstance(
        'old-instance',
        {timeoutMs:1000, intervalMs:0}
    );
    assert.equal(result.instance_id, 'new-instance');
    assert.equal(statusCalls, 2);
    console.log('Canvas backend restart tests passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
```

- [ ] **Step 2: Run the frontend test and verify it fails**

Run:

```powershell
node tests\canvas-backend-restart.test.js
```

Expected: failure reports the missing menu section or missing restart functions.

- [ ] **Step 3: Add restart menu markup and translations**

Append this block to the bottom of `#createMenu` after the Output item:

```html
<div id="backendRestartMenuSection" hidden>
    <div class="menu-divider"></div>
    <button id="backendRestartMenuBtn" class="menu-btn" type="button" onclick="restartCanvasBackend()">
        <i data-lucide="rotate-cw" class="w-4 h-4"></i>
        <span data-i18n="canvas.restartBackend">重启后台</span>
    </button>
</div>
```

Add exact translation keys to `static/js/i18n/canvas.js`:

```javascript
"canvas.restartBackend": { zh: "重启后台", en: "Restart backend" },
"canvas.restartBackendConfirm": {
    zh: "重启会中断当前生成任务，并让局域网用户短暂断开。只会重启 Infinite Canvas 后台，确定继续吗？",
    en: "Restarting interrupts active generation tasks and briefly disconnects LAN users. Only the Infinite Canvas backend will restart. Continue?"
},
"canvas.restartBackendWaiting": { zh: "后台正在重启，请稍候…", en: "Backend is restarting. Please wait…" },
"canvas.restartBackendTimeout": {
    zh: "后台未在 45 秒内恢复，请手动刷新页面或运行“启动服务.bat”。",
    en: "The backend did not recover within 45 seconds. Refresh manually or run 启动服务.bat."
},
"canvas.restartBackendFailed": { zh: "后台重启失败", en: "Backend restart failed" },
```

- [ ] **Step 4: Implement localhost visibility, polling, and restart action**

Add element references and state near the existing `createMenu` reference:

```javascript
const backendRestartMenuSection = document.getElementById('backendRestartMenuSection');
const backendRestartMenuBtn = document.getElementById('backendRestartMenuBtn');
let backendRestartInFlight = false;
```

Add the behavior:

```javascript
function isLocalRestartHost(hostname=window.location.hostname){
    const host = String(hostname || '').trim().toLowerCase().replace(/^\[|\]$/g, '');
    return host === 'localhost' || host === '127.0.0.1' || host === '::1';
}

function syncBackendRestartMenuVisibility(){
    if(!backendRestartMenuSection) return;
    backendRestartMenuSection.hidden = !isLocalRestartHost();
}

async function waitForBackendInstance(oldInstanceId, {timeoutMs=45000, intervalMs=800}={}){
    const deadline = Date.now() + timeoutMs;
    while(Date.now() < deadline){
        try {
            const response = await fetch('/api/system/status', {cache:'no-store'});
            if(response.ok){
                const data = await response.json();
                if(data?.instance_id && data.instance_id !== oldInstanceId) return data;
            }
        } catch(error) {
            // The old process is expected to become unreachable during restart.
        }
        await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
    throw new Error(tr('canvas.restartBackendTimeout'));
}

async function restartCanvasBackend(){
    if(backendRestartInFlight || !isLocalRestartHost()) return;
    closeCreateMenu();
    if(!window.confirm(tr('canvas.restartBackendConfirm'))) return;

    backendRestartInFlight = true;
    if(backendRestartMenuBtn) backendRestartMenuBtn.disabled = true;
    setStatus(tr('canvas.restartBackendWaiting'));
    try {
        const response = await fetch('/api/system/restart', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
        });
        if(!response.ok){
            throw new Error(await responseErrorMessage(response, tr('canvas.restartBackendFailed')));
        }
        const data = await response.json();
        await waitForBackendInstance(data.instance_id);
        window.location.reload();
    } catch(error) {
        showErrorModal(error?.message || tr('canvas.restartBackendFailed'), tr('canvas.restartBackendFailed'));
        setStatus('Ready');
    } finally {
        backendRestartInFlight = false;
        if(backendRestartMenuBtn) backendRestartMenuBtn.disabled = false;
    }
}
```

Call `syncBackendRestartMenuVisibility()` inside `openCreateMenu()` before the menu is opened. Do not add the action to node, port, image, link, or output context menus.

- [ ] **Step 5: Run frontend and localization tests**

Run:

```powershell
node tests\canvas-backend-restart.test.js
node static\js\i18n\validate-i18n.js
```

Expected: restart behavior tests pass and the localization validator reports no missing or duplicate keys.

- [ ] **Step 6: Commit the frontend unit**

```powershell
git add static/canvas.html static/js/canvas.js static/js/i18n/canvas.js tests/canvas-backend-restart.test.js
git commit -m "feat: add canvas backend restart action"
```

---

### Task 3: Verify regressions and perform a safe UI smoke test

**Files:**
- Modify only if a verification failure proves a defect in a Task 1 or Task 2 file.
- Review: `docs/superpowers/specs/2026-07-30-canvas-local-backend-restart-design.md`

**Interfaces:**
- Consumes: all Task 1 and Task 2 interfaces.
- Produces: a verified localhost-only restart flow with no regressions in existing canvas connection or loop-image behavior.

- [ ] **Step 1: Run the focused backend and frontend suites**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_backend_restart -v
node tests\canvas-backend-restart.test.js
node tests\canvas-generator-llm-connection.test.js
node tests\canvas-loop-llm-images.test.js
node static\js\i18n\validate-i18n.js
```

Expected: every command exits with code 0.

- [ ] **Step 2: Run static validation**

```powershell
.\.venv\Scripts\python.exe -m py_compile main.py tools\restart_backend.py
git diff --check
```

Expected: no syntax, whitespace, or conflict-marker errors.

- [ ] **Step 3: Inspect the blank-canvas menu without triggering restart**

Open `http://127.0.0.1:3000/`, enter an infinite canvas, and right-click a blank area.

Expected:

- “重启后台” appears at the bottom below a divider.
- Right-clicking a node does not add the restart action to node-specific menus.
- Canceling the confirmation leaves the backend and page unchanged.

- [ ] **Step 4: Verify LAN visibility with a deterministic function test**

Run:

```powershell
node -e "const fs=require('fs'),vm=require('vm');const s=fs.readFileSync('static/js/canvas.js','utf8');const a=s.indexOf('function isLocalRestartHost('),b=s.indexOf('\\nfunction ',a+1);const c={};vm.createContext(c);vm.runInContext(s.slice(a,b)+';this.f=isLocalRestartHost',c);if(c.f('192.168.2.88'))process.exit(1);"
```

Expected: exit code 0, confirming the menu visibility predicate rejects a LAN address.

- [ ] **Step 5: Review the final diff against the approved design**

Run:

```powershell
git diff -- main.py tools/restart_backend.py static/canvas.html static/js/canvas.js static/js/i18n/canvas.js tests/test_backend_restart.py tests/canvas-backend-restart.test.js docs/superpowers/specs/2026-07-30-canvas-local-backend-restart-design.md
```

Confirm the diff contains no API keys, provider credentials, unrelated node changes, or calls that restart external services.

- [ ] **Step 6: Commit verification metadata if the plan/spec is not already committed**

```powershell
git add docs/superpowers/specs/2026-07-30-canvas-local-backend-restart-design.md docs/superpowers/plans/2026-07-30-canvas-local-backend-restart.md
git commit -m "docs: record canvas backend restart design"
```
