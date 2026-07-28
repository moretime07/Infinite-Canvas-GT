import hashlib
import importlib
import marshal
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motion_extractor import models


class ContentionProbeRLock:
    """Report real lock contention without relying on scheduler timing."""

    def __init__(self, observations):
        self._lock = threading.RLock()
        self._observations = observations

    def __enter__(self):
        if not self._lock.acquire(blocking=False):
            self._observations.put(("waiting", threading.current_thread().name))
            self._lock.acquire()
        return self

    def __exit__(self, _error_type, _error, _traceback):
        self._lock.release()


class MotionModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_sys_path = sys.path.copy()
        self.original_dont_write_bytecode = sys.dont_write_bytecode
        self.original_pycache_prefix = sys.pycache_prefix
        self.loaded_module_names = set()
        self.cache_root = Path(self.temp_dir.name) / "cache"
        self.payload = b"verified-model-data"
        self.artifact = models.ModelArtifact(
            repo_id="test/example",
            filename="model.onnx",
            sha256=hashlib.sha256(self.payload).hexdigest(),
        )

    def tearDown(self):
        for module_name in self.loaded_module_names:
            sys.modules.pop(module_name, None)
        sys.path[:] = self.original_sys_path
        sys.dont_write_bytecode = self.original_dont_write_bytecode
        sys.pycache_prefix = self.original_pycache_prefix
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

    def make_source_fixture(self, name):
        source = models.GitSource(name, "https://example.invalid/source.git", "a" * 40)
        checkout = self.cache_root / "motion_models" / "sources" / source.name
        checkout.mkdir(parents=True)
        module_name = f"motion_runtime_{name.replace('-', '_')}"
        self.loaded_module_names.add(module_name)
        module_path = checkout / f"{module_name}.py"
        module_path.write_text("VALUE = 'verified-source'\n", encoding="utf-8")
        tracked_paths = {module_path.relative_to(checkout).as_posix()}

        def git_run(command, **_kwargs):
            if command[-2:] == ["rev-parse", "HEAD"]:
                return CompletedProcess(command, 0, source.commit + "\n", "")
            if "status" in command:
                untracked_paths = sorted(
                    path.relative_to(checkout).as_posix()
                    for path in checkout.rglob("*")
                    if path.is_file() and path.relative_to(checkout).as_posix() not in tracked_paths
                )
                status = "".join(f"?? {path}\0" for path in untracked_paths)
                return CompletedProcess(command, 0, status, "")
            self.fail(f"unexpected git command: {command}")

        return source, checkout, module_name, module_path, git_run

    @staticmethod
    def write_timestamp_bytecode(source_path, bytecode_path, code):
        source_stat = source_path.stat()
        header = (
            importlib.util.MAGIC_NUMBER
            + (0).to_bytes(4, "little")
            + int(source_stat.st_mtime).to_bytes(4, "little")
            + source_stat.st_size.to_bytes(4, "little")
        )
        bytecode_path.parent.mkdir(parents=True, exist_ok=True)
        bytecode_path.write_bytes(header + marshal.dumps(compile(code, str(source_path), "exec")))

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

    def test_ensure_motion_assets_returns_source_paths_without_mutating_import_state(self):
        source, checkout, _module_name, _module_path, git_run = self.make_source_fixture("asset-path")
        prior_path = sys.path.copy()
        prior_dont_write_bytecode = sys.dont_write_bytecode
        prior_pycache_prefix = str(Path(self.temp_dir.name) / "prior-bytecode")
        sys.pycache_prefix = prior_pycache_prefix

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models, "MODEL_ARTIFACTS", ()
        ), patch.object(models.subprocess, "run", side_effect=git_run):
            assets = models.ensure_motion_assets(
                self.cache_root, lambda _message, _progress: None, lambda: False
            )

        self.assertEqual(assets[source.name], checkout)
        self.assertEqual(sys.path, prior_path)
        self.assertEqual(sys.dont_write_bytecode, prior_dont_write_bytecode)
        self.assertEqual(sys.pycache_prefix, prior_pycache_prefix)

    def test_verified_source_imports_allow_clean_repeat_use(self):
        source, _checkout, module_name, _module_path, git_run = self.make_source_fixture("repeat")

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models, "MODEL_ARTIFACTS", ()
        ), patch.object(models.subprocess, "run", side_effect=git_run):
            assets = models.ensure_motion_assets(
                self.cache_root, lambda _message, _progress: None, lambda: False
            )
            imported_values = []
            for _attempt in range(2):
                sys.modules.pop(module_name, None)
                with models.verified_source_imports(self.cache_root, assets):
                    imported_values.append(importlib.import_module(module_name).VALUE)

        self.assertEqual(imported_values, ["verified-source", "verified-source"])

    def test_verified_source_imports_create_no_in_tree_or_external_bytecode(self):
        source, checkout, module_name, _module_path, git_run = self.make_source_fixture("no-bytecode")
        external_bytecode = Path(self.temp_dir.name) / "external-bytecode"
        sys.pycache_prefix = str(external_bytecode)

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models, "MODEL_ARTIFACTS", ()
        ), patch.object(models.subprocess, "run", side_effect=git_run):
            assets = models.ensure_motion_assets(
                self.cache_root, lambda _message, _progress: None, lambda: False
            )
            with models.verified_source_imports(self.cache_root, assets):
                importlib.import_module(module_name)

        self.assertEqual(list(checkout.rglob("*.pyc")), [])
        self.assertEqual(list(external_bytecode.rglob("*.pyc")), [])

    def test_verified_source_imports_never_execute_forged_external_bytecode(self):
        source, _checkout, module_name, module_path, git_run = self.make_source_fixture("external-forgery")
        external_bytecode = Path(self.temp_dir.name) / "external-bytecode"
        sys.pycache_prefix = str(external_bytecode)
        forged_bytecode = Path(importlib.util.cache_from_source(str(module_path)))
        self.write_timestamp_bytecode(module_path, forged_bytecode, "VALUE = 'forged-bytecode'\n")

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models, "MODEL_ARTIFACTS", ()
        ), patch.object(models.subprocess, "run", side_effect=git_run):
            assets = models.ensure_motion_assets(
                self.cache_root, lambda _message, _progress: None, lambda: False
            )
            with models.verified_source_imports(self.cache_root, assets):
                imported = importlib.import_module(module_name)

        self.assertEqual(imported.VALUE, "verified-source")

    def test_verified_source_imports_restore_interpreter_state_after_success(self):
        source, checkout, _module_name, _module_path, git_run = self.make_source_fixture("restore-success")
        prior_path = sys.path.copy()
        prior_dont_write_bytecode = False
        prior_pycache_prefix = str(Path(self.temp_dir.name) / "prior-bytecode")
        sys.dont_write_bytecode = prior_dont_write_bytecode
        sys.pycache_prefix = prior_pycache_prefix

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models, "MODEL_ARTIFACTS", ()
        ), patch.object(models.subprocess, "run", side_effect=git_run):
            assets = models.ensure_motion_assets(
                self.cache_root, lambda _message, _progress: None, lambda: False
            )
            with models.verified_source_imports(self.cache_root, assets):
                self.assertEqual(sys.path[0], str(checkout))
                self.assertTrue(sys.dont_write_bytecode)
                self.assertIsNone(sys.pycache_prefix)

        self.assertEqual(sys.path, prior_path)
        self.assertEqual(sys.dont_write_bytecode, prior_dont_write_bytecode)
        self.assertEqual(sys.pycache_prefix, prior_pycache_prefix)

    def test_verified_source_imports_restore_interpreter_state_after_exception(self):
        source, _checkout, _module_name, _module_path, git_run = self.make_source_fixture("restore-error")
        prior_path = sys.path.copy()
        prior_dont_write_bytecode = False
        prior_pycache_prefix = str(Path(self.temp_dir.name) / "prior-bytecode")
        sys.dont_write_bytecode = prior_dont_write_bytecode
        sys.pycache_prefix = prior_pycache_prefix

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models, "MODEL_ARTIFACTS", ()
        ), patch.object(models.subprocess, "run", side_effect=git_run):
            assets = models.ensure_motion_assets(
                self.cache_root, lambda _message, _progress: None, lambda: False
            )
            with self.assertRaisesRegex(RuntimeError, "processor failed"):
                with models.verified_source_imports(self.cache_root, assets):
                    raise RuntimeError("processor failed")

        self.assertEqual(sys.path, prior_path)
        self.assertEqual(sys.dont_write_bytecode, prior_dont_write_bytecode)
        self.assertEqual(sys.pycache_prefix, prior_pycache_prefix)

    def test_verified_source_imports_revalidate_checkout_after_use(self):
        source, checkout, _module_name, _module_path, git_run = self.make_source_fixture("revalidate")

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models, "MODEL_ARTIFACTS", ()
        ), patch.object(models.subprocess, "run", side_effect=git_run):
            assets = models.ensure_motion_assets(
                self.cache_root, lambda _message, _progress: None, lambda: False
            )
            with self.assertRaises(models.MotionSourceError):
                with models.verified_source_imports(self.cache_root, assets):
                    (checkout / "injected.py").write_text("VALUE = 'injected'\n", encoding="utf-8")

    def run_overlapping_import_contexts(self, first_raises):
        source, checkout, _module_name, _module_path, git_run = self.make_source_fixture(
            "thread-error" if first_raises else "thread-success"
        )
        assets = {source.name: checkout}
        prior_path = [str(Path(self.temp_dir.name) / "path-sentinel"), *sys.path]
        prior_dont_write_bytecode = False
        prior_pycache_prefix = str(Path(self.temp_dir.name) / "prior-bytecode")
        sys.path[:] = prior_path
        sys.dont_write_bytecode = prior_dont_write_bytecode
        sys.pycache_prefix = prior_pycache_prefix

        observations = queue.Queue()
        worker_errors = queue.Queue()
        first_inside = threading.Event()
        second_inside = threading.Event()
        release_first = threading.Event()
        overlap_barrier = threading.Barrier(2)
        probe_lock = ContentionProbeRLock(observations)

        def first_worker():
            try:
                with models.verified_source_imports(self.cache_root, assets):
                    first_inside.set()
                    overlap_barrier.wait(timeout=5)
                    if not release_first.wait(timeout=5):
                        raise AssertionError("test did not release the first import context")
                    if first_raises:
                        raise RuntimeError("processor failed")
            except RuntimeError as error:
                if not first_raises or str(error) != "processor failed":
                    worker_errors.put(error)
            except BaseException as error:
                worker_errors.put(error)

        def second_worker():
            try:
                if not first_inside.wait(timeout=5):
                    raise AssertionError("first import context did not enter")
                overlap_barrier.wait(timeout=5)
                with models.verified_source_imports(self.cache_root, assets):
                    second_inside.set()
                    observations.put(("entered", threading.current_thread().name))
                    self.assertEqual(sys.path[0], str(checkout))
                    self.assertTrue(sys.dont_write_bytecode)
                    self.assertIsNone(sys.pycache_prefix)
            except BaseException as error:
                worker_errors.put(error)

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models, "_VERIFIED_SOURCE_IMPORT_LOCK", probe_lock, create=True
        ), patch.object(models.subprocess, "run", side_effect=git_run):
            first = threading.Thread(target=first_worker, name="first-import")
            second = threading.Thread(target=second_worker, name="second-import")
            first.start()
            second.start()
            try:
                self.assertEqual(observations.get(timeout=5), ("waiting", "second-import"))
                self.assertFalse(second_inside.is_set())
            finally:
                release_first.set()
                first.join(timeout=5)
                second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(second_inside.is_set())
        self.assertEqual(observations.get_nowait(), ("entered", "second-import"))
        self.assertTrue(worker_errors.empty(), list(worker_errors.queue))
        self.assertEqual(sys.path, prior_path)
        self.assertEqual(sys.dont_write_bytecode, prior_dont_write_bytecode)
        self.assertEqual(sys.pycache_prefix, prior_pycache_prefix)

    def test_verified_source_imports_serialize_overlapping_success_contexts(self):
        self.run_overlapping_import_contexts(first_raises=False)

    def test_verified_source_imports_serialize_when_a_processor_raises(self):
        self.run_overlapping_import_contexts(first_raises=True)

    def test_verified_source_imports_support_nested_same_thread_reentry(self):
        source, checkout, _module_name, _module_path, git_run = self.make_source_fixture("nested")
        assets = {source.name: checkout}
        prior_path = sys.path.copy()
        prior_dont_write_bytecode = sys.dont_write_bytecode
        prior_pycache_prefix = sys.pycache_prefix

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models.subprocess, "run", side_effect=git_run
        ):
            with models.verified_source_imports(self.cache_root, assets):
                with models.verified_source_imports(self.cache_root, assets):
                    self.assertEqual(sys.path[0], str(checkout))
                    self.assertTrue(sys.dont_write_bytecode)
                    self.assertIsNone(sys.pycache_prefix)
                self.assertEqual(sys.path[0], str(checkout))
                self.assertTrue(sys.dont_write_bytecode)
                self.assertIsNone(sys.pycache_prefix)

        self.assertEqual(sys.path, prior_path)
        self.assertEqual(sys.dont_write_bytecode, prior_dont_write_bytecode)
        self.assertEqual(sys.pycache_prefix, prior_pycache_prefix)

    def test_rejects_forged_valid_header_bytecode_inside_checkout(self):
        source, checkout, _module_name, module_path, git_run = self.make_source_fixture("inside-forgery")
        sys.pycache_prefix = None
        bytecode_path = Path(importlib.util.cache_from_source(str(module_path)))
        self.write_timestamp_bytecode(module_path, bytecode_path, "VALUE = 'forged-bytecode'\n")
        assets = {source.name: checkout}

        with patch.object(models, "GIT_SOURCES", (source,)), patch.object(
            models.subprocess, "run", side_effect=git_run
        ):
            with self.assertRaises(models.MotionSourceError):
                with models.verified_source_imports(self.cache_root, assets):
                    self.fail("a checkout containing bytecode must not enter the import scope")

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
