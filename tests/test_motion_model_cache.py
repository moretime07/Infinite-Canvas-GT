import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motion_extractor import models


class MotionModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_root = Path(self.temp_dir.name) / "cache"
        self.payload = b"verified-model-data"
        self.artifact = models.ModelArtifact(
            repo_id="test/example",
            filename="model.onnx",
            sha256=hashlib.sha256(self.payload).hexdigest(),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def ensure(self, downloader, cancelled=lambda: False):
        with patch.object(models, "MODEL_ARTIFACTS", (self.artifact,)), patch.object(
            models, "GIT_SOURCES", ()
        ), patch.object(models, "hf_hub_download", downloader):
            return models.ensure_motion_assets(self.cache_root, lambda _message, _progress: None, cancelled)

    def make_downloader(self, payload=None, calls=None):
        source = Path(self.temp_dir.name) / "downloaded-model"
        source.write_bytes(self.payload if payload is None else payload)

        def download(**_kwargs):
            if calls is not None:
                calls.append(True)
            return str(source)

        return download

    def test_reuses_valid_preexisting_model_without_downloading(self):
        destination = self.cache_root / "motion_models" / self.artifact.filename
        destination.parent.mkdir(parents=True)
        destination.write_bytes(self.payload)

        def downloader(**_kwargs):
            self.fail("a verified cached model must not be downloaded again")

        assets = self.ensure(downloader)

        self.assertEqual(assets[self.artifact.filename], destination)
        self.assertEqual(destination.read_bytes(), self.payload)

    def test_replaces_wrong_hash_with_a_new_verified_download(self):
        destination = self.cache_root / "motion_models" / self.artifact.filename
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"corrupted")
        calls = []

        assets = self.ensure(self.make_downloader(calls=calls))

        self.assertEqual(calls, [True])
        self.assertEqual(assets[self.artifact.filename].read_bytes(), self.payload)
        self.assertFalse(destination.with_suffix(destination.suffix + ".part").exists())

    def test_never_accepts_an_interrupted_part_file(self):
        part_file = self.cache_root / "motion_models" / f"{self.artifact.filename}.part"
        part_file.parent.mkdir(parents=True)
        part_file.write_bytes(self.payload)
        calls = []

        assets = self.ensure(self.make_downloader(calls=calls))

        self.assertEqual(calls, [True])
        self.assertTrue(assets[self.artifact.filename].is_file())
        self.assertFalse(part_file.exists())

    def test_promotes_download_atomically_only_after_hash_validation(self):
        destination = self.cache_root / "motion_models" / self.artifact.filename
        original_replace = os.replace
        observations = []

        def checked_replace(source, target):
            observations.append((Path(source).is_file(), Path(target).exists()))
            return original_replace(source, target)

        with patch.object(models, "os") as mocked_os:
            mocked_os.replace.side_effect = checked_replace
            mocked_os.path = os.path
            self.ensure(self.make_downloader())

        self.assertEqual(observations, [(True, False)])
        self.assertEqual(destination.read_bytes(), self.payload)

    def test_cancellation_leaves_no_valid_looking_final_file(self):
        cancelled = {"value": False}
        source = Path(self.temp_dir.name) / "downloaded-model"
        source.write_bytes(self.payload)

        def downloader(**_kwargs):
            cancelled["value"] = True
            return str(source)

        with self.assertRaises(models.MotionCancelled):
            self.ensure(downloader, cancelled=lambda: cancelled["value"])

        destination = self.cache_root / "motion_models" / self.artifact.filename
        self.assertFalse(destination.exists())

    def test_rejects_source_checkout_at_a_different_commit(self):
        source = models.GitSource(
            name="example-source",
            url="https://example.invalid/source.git",
            commit="a" * 40,
        )
        checkout = self.cache_root / "motion_sources" / source.name
        checkout.mkdir(parents=True)
        completed = CompletedProcess(
            args=["git", "rev-parse", "HEAD"],
            returncode=0,
            stdout="b" * 40 + "\n",
            stderr="",
        )

        with patch.object(models.subprocess, "run", return_value=completed):
            with self.assertRaises(models.MotionSourceError):
                models.verify_source_checkout(checkout, source)

    def test_runtime_status_serialization_has_names_without_absolute_paths(self):
        status = models.MotionRuntimeStatus(
            ready=False,
            cuda_available=False,
            onnx_cuda_available=False,
            missing_packages=("torch", "onnxruntime"),
            missing_models=("model.onnx",),
        )

        serialized = status.to_dict()

        self.assertEqual(serialized["missing_packages"], ["torch", "onnxruntime"])
        self.assertEqual(serialized["missing_models"], ["model.onnx"])
        self.assertNotIn(str(self.cache_root.resolve()), repr(serialized))


if __name__ == "__main__":
    unittest.main()
