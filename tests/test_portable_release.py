import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.portable.release_assets import ARCHIVE_NAME, split_archive


@unittest.skipUnless(os.name == "nt", "Windows PowerShell merger")
class PortableReleaseTests(unittest.TestCase):
    def prepare(self, root):
        archive = root / ARCHIVE_NAME
        payload = b"PK-portable-test-content" * 100
        archive.write_bytes(payload)
        release = root / "download"
        manifest = split_archive(archive, release, part_size=1500)
        self.assertEqual(manifest["sha256"], hashlib.sha256(payload).hexdigest())
        return release, payload

    def run_merge(self, root):
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        return subprocess.run([str(shell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(root / "Merge-Bundle.ps1")], capture_output=True, timeout=30)

    def test_merge_and_reuse_verified_zip(self):
        with tempfile.TemporaryDirectory(prefix="portable-release-") as folder:
            release, payload = self.prepare(Path(folder))
            result = self.run_merge(release)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
            self.assertEqual((release / ARCHIVE_NAME).read_bytes(), payload)
            self.assertEqual(self.run_merge(release).returncode, 0)

    def test_bad_part_does_not_create_output(self):
        with tempfile.TemporaryDirectory(prefix="portable-release-") as folder:
            release, _ = self.prepare(Path(folder))
            (release / (ARCHIVE_NAME + ".002")).write_bytes(b"bad")
            self.assertNotEqual(self.run_merge(release).returncode, 0)
            self.assertFalse((release / ARCHIVE_NAME).exists())

    def test_never_overwrites_different_existing_zip(self):
        with tempfile.TemporaryDirectory(prefix="portable-release-") as folder:
            release, _ = self.prepare(Path(folder))
            (release / ARCHIVE_NAME).write_bytes(b"keep existing archive")
            self.assertNotEqual(self.run_merge(release).returncode, 0)
            self.assertEqual((release / ARCHIVE_NAME).read_bytes(), b"keep existing archive")

    def test_rejects_manifest_path_traversal(self):
        with tempfile.TemporaryDirectory(prefix="portable-release-") as folder:
            release, _ = self.prepare(Path(folder))
            path = release / "bundle.json"
            manifest = json.loads(path.read_text())
            manifest["parts"][0]["name"] = "../outside.dat"
            path.write_text(json.dumps(manifest))
            self.assertNotEqual(self.run_merge(release).returncode, 0)
            self.assertFalse((release / ARCHIVE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
