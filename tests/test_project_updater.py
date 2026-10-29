import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import shutil
import sys
import threading
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import main
import project_updater as updater


def write(root, name, value):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


class ProgramUpdateTests(unittest.TestCase):
    def test_personal_paths_and_traversal_are_rejected(self):
        for name in ("API/.env", "data/api_providers.json", "assets/local.png", "output/a.mp4",
                     "history.json", "global_config.json", ".venv/a.py", "tmp/a.py", ".git/config",
                     "../main.py", "/main.py", "static/../main.py", "static//a.js", "static\\a.js",
                     "static/file.js:secret", "static/.env", "static/token.key", "static/end./a.js"):
            with self.subTest(name=name):
                self.assertFalse(updater.allowed_file(name))
        for name in ("main.py", "aiide_video.py", "project_updater.py", "VERSION", "requirements.txt",
                     "tools/restart_backend.py", "tools/install_windows.ps1", "启动服务.bat",
                     "motion_extractor/service.py", "static/js/canvas.js", "workflows/example.json"):
            self.assertTrue(updater.allowed_file(name), name)

    def test_download_is_pinned_and_hash_checked(self):
        commit = "a" * 40
        contents = {name: b"example" for name in updater.REQUIRED}
        contents["data/private.json"] = b"must-not-download"
        entries = [{"path": name, "type": "blob", "mode": "100644", "size": len(data),
                    "sha": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()}
                   for name, data in contents.items()]
        calls = []

        def reader(url):
            calls.append(url)
            if url.endswith("commits/main"):
                return json.dumps({"sha": commit}).encode()
            if "/git/trees/" in url:
                return json.dumps({"tree": entries}).encode()
            self.assertIn("/" + commit + "/", url)
            return contents[url.split(commit + "/", 1)[1]]

        with tempfile.TemporaryDirectory() as directory:
            revision, files = updater.download_snapshot(Path(directory), reader)
            self.assertEqual(revision, commit)
            self.assertEqual(set(files), updater.REQUIRED)
            self.assertFalse(any("private.json" in url for url in calls))
        entries[0]["sha"] = "b" * 40
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "integrity"):
            updater.download_snapshot(Path(directory), reader)

    def test_partial_or_linked_snapshot_is_rejected(self):
        for tree in ({"truncated": True, "tree": []}, {"tree": []}):
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
                updater.download_snapshot(Path(directory), lambda url: json.dumps(
                    {"sha": "a" * 40} if "commits/main" in url else tree).encode())

    def test_update_and_rollback_preserve_all_personal_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root, stage = Path(directory) / "app", Path(directory) / "stage"
            write(root, "main.py", "old")
            private = {"API/.env": "private-placeholder", "data/canvas.json": "canvas",
                       "assets/file.png": "media", "output/video.mp4": "video", "static/custom-user.js": "custom"}
            for name, value in private.items():
                write(root, name, value)
            write(stage, "main.py", "new")
            write(stage, "new_module.py", "added")
            result = updater.apply_files(root, stage, ["main.py", "new_module.py"], "a" * 40)
            backup = Path(result["backup_dir"])
            self.assertEqual((root / "main.py").read_text(), "new")
            self.assertFalse((backup / "API").exists())
            updater.rollback(root, backup.name)
            self.assertEqual((root / "main.py").read_text(), "old")
            self.assertFalse((root / "new_module.py").exists())
            for name, value in private.items():
                self.assertEqual((root / name).read_text(), value)

    def test_mid_apply_failure_restores_code_and_removes_added_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root, stage = Path(directory) / "app", Path(directory) / "stage"
            write(root, "main.py", "old")
            for name in ("added.py", "main.py", "VERSION"):
                write(stage, name, "new")
            copy = updater.atomic_copy

            def fail_version(source, target):
                if target.name == "VERSION":
                    raise OSError("simulated full disk")
                return copy(source, target)

            with patch.object(updater, "atomic_copy", side_effect=fail_version), self.assertRaises(OSError):
                updater.apply_files(root, stage, ["added.py", "main.py", "VERSION"])
            self.assertEqual((root / "main.py").read_text(), "old")
            self.assertFalse((root / "added.py").exists())

    def test_validation_failure_never_replaces_live_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root, "main.py", "old")

            def download(stage):
                write(stage, "main.py", "new")
                write(stage, "VERSION", "2026.10.29.1")
                return "a" * 40, ["main.py", "VERSION"]

            def invalid(*args):
                raise RuntimeError("bad import")

            with self.assertRaises(RuntimeError):
                updater.install(root, downloader=download, validator=invalid)
            self.assertEqual((root / "main.py").read_text(), "old")
            self.assertEqual(list((root / "data/update_staging").iterdir()), [])

    def test_rollback_rejects_tampered_backups_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root, stage = Path(directory) / "app", Path(directory) / "stage"
            write(root, "main.py", "old")
            write(stage, "main.py", "new")
            result = updater.apply_files(root, stage, ["main.py"])
            backup = Path(result["backup_dir"])
            write(backup, "main.py", "tampered")
            with self.assertRaisesRegex(ValueError, "integrity"):
                updater.rollback(root, backup.name)
            with self.assertRaises(ValueError):
                updater.rollback(root, "../API")
            self.assertEqual((root / "main.py").read_text(), "new")

    def test_existing_lock_blocks_concurrent_installs(self):
        with tempfile.TemporaryDirectory() as directory:
            with updater.local_lock(directory):
                with self.assertRaises(OSError):
                    with updater.local_lock(directory):
                        self.fail("lock was not exclusive")

    def test_full_snapshot_import_and_old_installation_update_without_network(self):
        source_root = Path(__file__).resolve().parents[1]
        files = [p.name for p in source_root.iterdir() if p.is_file() and updater.allowed_file(p.name)]
        for folder in ("static", "tools", "motion_extractor", "workflows"):
            files.extend(p.relative_to(source_root).as_posix() for p in (source_root / folder).rglob("*")
                         if p.is_file() and updater.allowed_file(p.relative_to(source_root).as_posix()))
        with tempfile.TemporaryDirectory(prefix="GT old installation ") as directory:
            root = Path(directory)
            write(root, "main.py", "# original installation")
            write(root, "requirements.txt", (source_root / "requirements.txt").read_text())
            write(root, "API/.env", "PRIVATE_KEY=local-only-placeholder")
            write(root, "data/canvas.json", "private canvas")

            def download(stage):
                for name in files:
                    target = updater.code_path(stage, name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_root / name, target)
                return "a" * 40, files

            result = updater.install(root, sys.executable, downloader=download)
            self.assertEqual(result["version"], (source_root / "VERSION").read_text(encoding="utf-8-sig").strip())
            self.assertTrue((root / "motion_extractor/service.py").is_file())
            self.assertTrue((root / "project_updater.py").is_file())
            self.assertTrue((root / "video_download.py").is_file())
            self.assertEqual((root / "API/.env").read_text(), "PRIVATE_KEY=local-only-placeholder")
            self.assertEqual((root / "data/canvas.json").read_text(), "private canvas")
            updater.rollback(root, Path(result["backup_dir"]).name)
            self.assertEqual((root / "main.py").read_text(), "# original installation")
            self.assertFalse((root / "project_updater.py").exists())


HEADERS = {"Origin": "http://127.0.0.1:3000", "X-Infinite-Canvas-Restart": "1"}


class UpdateApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.restart = patch.object(main, "BACKEND_RESTART_SCHEDULED", False)
        self.restart.start()
        self.addCleanup(self.restart.stop)

    async def request(self, headers=None, client="127.0.0.1", payload=None):
        transport = httpx.ASGITransport(app=main.app, client=(client, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:3000") as api:
            return await api.post("/api/update-from-github", headers=headers or {}, json=payload or {})

    async def test_only_local_same_origin_user_can_update(self):
        with patch.object(updater, "install") as install:
            for headers, client in (({}, "127.0.0.1"), (HEADERS, "192.168.1.5"),
                                    ({**HEADERS, "Origin": "https://evil.example"}, "127.0.0.1")):
                response = await self.request(headers, client)
                self.assertEqual(response.status_code, 403)
            install.assert_not_called()

    async def test_legacy_source_cannot_change_the_gt_update_source(self):
        with patch.object(updater, "install", return_value={"count": 12, "updated": [], "version": "2026.10.29.1"}) as install, \
                patch.object(main, "schedule_backend_restart", return_value=True) as restart:
            response = await self.request(HEADERS, payload={"source": "modelscope", "fallback": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "github")
        self.assertEqual(response.json()["previous_instance_id"], main.APP_INSTANCE_ID)
        self.assertFalse(response.json()["fallback_used"])
        install.assert_called_once_with(main.BASE_DIR, main.sys.executable)
        restart.assert_called_once()

    async def test_running_video_blocks_update(self):
        with patch.dict(main.CANVAS_TASKS, {"test": {"status": "running"}}, clear=True), patch.object(updater, "install") as install:
            response = await self.request(HEADERS)
        self.assertEqual(response.status_code, 409)
        install.assert_not_called()

    async def test_active_motion_and_requests_block_update(self):
        with patch.object(main.MOTION_TASK_SERVICE, "has_active_tasks", new=AsyncMock(return_value=True)):
            self.assertEqual((await self.request(HEADERS)).status_code, 409)
        with patch.object(main, "UPDATE_ACTIVE_REQUESTS", 1):
            self.assertEqual((await self.request(HEADERS)).status_code, 409)

    async def test_update_failure_never_schedules_restart(self):
        with patch.object(updater, "install", side_effect=OSError("offline")), patch.object(main, "schedule_backend_restart") as restart:
            response = await self.request(HEADERS)
        self.assertEqual(response.status_code, 500)
        restart.assert_not_called()
        self.assertFalse(main.UPDATE_LOCK.locked())

    async def test_disconnect_does_not_abandon_file_update_worker(self):
        started, release = threading.Event(), threading.Event()

        def operation():
            started.set()
            release.wait(5)
            return "finished"

        worker = asyncio.create_task(main.run_update_worker(operation))
        await asyncio.to_thread(started.wait, 5)
        worker.cancel()
        await asyncio.sleep(0)
        self.assertFalse(worker.done())
        release.set()
        self.assertEqual(await worker, "finished")

    async def test_manual_restart_is_blocked_while_program_files_are_updating(self):
        main.UPDATE_LOCK.acquire()
        try:
            transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:3000") as api:
                response = await api.post("/api/system/restart", headers=HEADERS)
            self.assertEqual(response.status_code, 409)
        finally:
            main.UPDATE_LOCK.release()

    def test_version_checks_and_metadata_only_use_gt(self):
        with patch.object(main, "fetch_remote_version", return_value={"ok": True, "version": "2099.01.01"}) as fetch, \
                patch.object(main, "fetch_update_notes_with_fallback", return_value=({}, {})):
            self.assertTrue(main.check_update()["update_available"])
        fetch.assert_called_once_with(main.GITHUB_VERSION_URL, timeout=5.0)
        self.assertEqual(set(main.app_info()["sources"]), {"github"})
        self.assertIn("moretime07/Infinite-Canvas-GT", main.GITHUB_VERSION_URL)


if __name__ == "__main__":
    unittest.main()
