import tempfile
import unittest
from pathlib import Path

from tools.check_publish_safety import TOKEN_PATTERNS, local_secrets, private_path


class PublishSafetyTests(unittest.TestCase):
    def test_runtime_paths_are_private(self):
        for name in ("API/.env", ".env.production", "data/canvases/a.json",
                     "assets/library/private.png", "assets/uploads/private.mp4",
                     "output/video.mp4", "global_config.json", "history.json",
                     "shortcut.lnk", "backup.zip", ".venv/pyvenv.cfg",
                     "final-fix-private-review/report.md"):
            with self.subTest(name=name):
                self.assertTrue(private_path(name))

    def test_source_and_bundled_public_assets_are_allowed(self):
        for name in ("main.py", "README.md", "requirements.txt",
                     "static/images/logo.png", "workflows/example.json"):
            with self.subTest(name=name):
                self.assertFalse(private_path(name))

    def test_reads_local_secrets_but_skips_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "API").mkdir()
            (root / "API/.env").write_text(
                '# comment\nAPI_KEY="' + 'private-' + 'x' * 24 + '"\n'
                'OTHER_TOKEN=example-token\nBASE_URL=https://example.test\n', encoding="utf-8")
            self.assertEqual(local_secrets(root), {b"private-" + b"x" * 24})

    def test_detects_common_token_formats(self):
        for token in (b"sk-" + b"x" * 40, b"ghp_" + b"a" * 36, b"AKID" + b"z" * 32):
            self.assertTrue(any(pattern.search(token) for pattern in TOKEN_PATTERNS))


if __name__ == "__main__":
    unittest.main()
