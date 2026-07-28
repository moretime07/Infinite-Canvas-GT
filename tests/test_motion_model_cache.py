import hashlib
import os
import subprocess
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
        def download(**kwargs):
            if calls is not None:
                calls.append(kwargs)
            download_root = Path(kwargs.get("local_dir", self.temp_dir.name))
            source = download_root / self.artifact.filename
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(self.payload if payload is None else payload)
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

        self.assertEqual(len(calls), 1)
        self.assertEqual(assets[self.artifact.filename].read_bytes(), self.payload)
        self.assertFalse(destination.with_suffix(destination.suffix + ".part").exists())

    def test_never_accepts_an_interrupted_part_file(self):
        part_file = self.cache_root / "motion_models" / f"{self.artifact.filename}.part"
        part_file.parent.mkdir(parents=True)
        part_file.write_bytes(self.payload)
        calls = []

        assets = self.ensure(self.make_downloader(calls=calls))

        self.assertEqual(len(calls), 1)
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

    def test_directs_huggingface_download_storage_below_the_cache_root(self):
        calls = []

        self.ensure(self.make_downloader(calls=calls))

        self.assertEqual(len(calls), 1)
        for key in ("cache_dir", "local_dir"):
            self.assertTrue(Path(calls[0][key]).resolve().is_relative_to(self.cache_root.resolve()))

    def test_wrong_hash_never_promotes_a_final_model_file(self):
        destination = self.cache_root / "motion_models" / self.artifact.filename
        replacement_calls = []

        with patch.object(models.os, "replace", side_effect=lambda *_args: replacement_calls.append(True)):
            with self.assertRaises(models.MotionIntegrityError):
                self.ensure(self.make_downloader(payload=b"invalid-model-data"))

        self.assertEqual(replacement_calls, [])
        self.assertFalse(destination.exists())

    def test_cancellation_leaves_no_valid_looking_final_file(self):
        cancelled = {"value": False}
        def downloader(**kwargs):
            source = Path(kwargs.get("local_dir", self.temp_dir.name)) / self.artifact.filename
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(self.payload)
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
        checkout = self.cache_root / "motion_models" / "sources" / source.name
        checkout.mkdir(parents=True)
        completed = CompletedProcess(
            args=["git", "rev-parse", "HEAD"],
            returncode=0,
            stdout="b" * 40 + "\n",
            stderr="",
        )

        with patch.object(models.subprocess, "run", return_value=completed):
            with self.assertRaises(models.MotionSourceError):
                models.verify_source_checkout(checkout, source, checkout.parent)

    def test_places_source_checkouts_below_the_model_cache(self):
        source = models.GitSource(
            name="example-source",
            url="https://example.invalid/source.git",
            commit="a" * 40,
        )

        def git_run(command, **_kwargs):
            if command[1] == "clone":
                Path(command[-1]).mkdir(parents=True)
            if command[-2:] == ["rev-parse", "HEAD"]:
                return CompletedProcess(command, 0, source.commit + "\n", "")
            return CompletedProcess(command, 0, "", "")

        with patch.object(models.subprocess, "run", side_effect=git_run):
            checkout = models.ensure_source_checkout(self.cache_root, source)

        self.assertEqual(checkout, self.cache_root / "motion_models" / "sources" / source.name)

    def test_rejects_dirty_tracked_source_checkout(self):
        source = models.GitSource("example-source", "https://example.invalid/source.git", "a" * 40)
        checkout = self.cache_root / "motion_models" / "sources" / source.name
        checkout.mkdir(parents=True)

        def git_run(command, **_kwargs):
            if command[-2:] == ["rev-parse", "HEAD"]:
                return CompletedProcess(command, 0, source.commit + "\n", "")
            return CompletedProcess(command, 0, " M changed.py\n", "")

        with patch.object(models.subprocess, "run", side_effect=git_run):
            with self.assertRaises(models.MotionSourceError):
                models.verify_source_checkout(checkout, source, checkout.parent)

    def test_rejects_source_checkout_with_untracked_injected_file(self):
        source = models.GitSource("example-source", "https://example.invalid/source.git", "a" * 40)
        checkout = self.cache_root / "motion_models" / "sources" / source.name
        checkout.mkdir(parents=True)

        def git_run(command, **_kwargs):
            if command[-2:] == ["rev-parse", "HEAD"]:
                return CompletedProcess(command, 0, source.commit + "\n", "")
            return CompletedProcess(command, 0, "?? injected.py\n", "")

        with patch.object(models.subprocess, "run", side_effect=git_run):
            with self.assertRaises(models.MotionSourceError):
                models.verify_source_checkout(checkout, source, checkout.parent)

    def test_rejects_source_checkout_path_escape(self):
        source = models.GitSource("../outside", "https://example.invalid/source.git", "a" * 40)

        with self.assertRaises(models.MotionSourceError):
            models.ensure_source_checkout(self.cache_root, source)

    def test_rejects_source_checkout_symlink_escape(self):
        source = models.GitSource("example-source", "https://example.invalid/source.git", "a" * 40)
        checkout = self.cache_root / "motion_models" / "sources" / source.name
        checkout.parent.mkdir(parents=True)
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir()
        try:
            os.symlink(outside, checkout, target_is_directory=True)
        except OSError as error:
            junction = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(checkout), str(outside)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(junction.returncode, 0, f"could not create an escaping link: {error}")

        with self.assertRaises(models.MotionSourceError):
            models.ensure_source_checkout(self.cache_root, source)

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
