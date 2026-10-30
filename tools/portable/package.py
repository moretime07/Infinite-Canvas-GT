"""Assemble a portable release from an audited app ZIP and fresh public runtimes.

Never recursively copy the developer's app directory. User data is excluded by
construction. Only the verified public motion assets are allowed under data/.
Run with the staged Python so the installed-package manifest matches the bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.check_publish_safety import local_secrets, private_path, TOKEN_PATTERNS
from motion_extractor.models import MODEL_ARTIFACTS, GIT_SOURCES, ensure_motion_assets

PUBLIC_SOURCE_ZIP_SHA = "ec9526b1f4bd49b012a797e13c47179e845e83c31e71be8aaa273f35dff0a2ce"
OVERLAYS = ("main.py", "portable_runtime.py", "requirements-motion.txt", "README.md", ".gitignore",
            "VERSION", "static/update-notes.json", "tools/check_publish_safety.py")
VENDOR_ROOTS = ("python", "bin/ffmpeg", "bin/git", "third-party-sources")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_file(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def files(root: Path):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            path = Path(base) / name
            if path.suffix.lower() not in {".pyc", ".pyo", ".log"}:
                if path.is_symlink():
                    raise RuntimeError("Symlinks are not accepted in the staged distribution")
                yield path


def copy_tree(source: Path, destination: Path, *, python=False) -> None:
    if not source.is_dir():
        raise RuntimeError("Missing staged component: " + source.name)
    for file in files(source):
        relative = file.relative_to(source)
        # pip's generated console EXEs embed the build machine's absolute path.
        # They are replaced below with relocatable batch wrappers from metadata.
        if python and relative.parts[0].lower() == "scripts":
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)


def runtime_distributions(destination: Path):
    # Never include packages from the builder's per-user site-packages.
    return importlib.metadata.distributions(path=[str(destination / "python/Lib/site-packages")])


def install_console_wrappers(destination: Path) -> None:
    scripts = destination / "python/Scripts"
    scripts.mkdir(exist_ok=True)
    for distribution in runtime_distributions(destination):
        for entry in distribution.entry_points:
            if entry.group != "console_scripts" or not re.fullmatch(r"[\w.-]+", entry.name):
                continue
            if not re.fullmatch(r"[\w.]+:[\w.]+", entry.value):
                continue
            module, function = entry.value.split(":")
            base, _, attr = function.partition(".")
            call = base + ("." + attr if attr else "")
            code = f"import sys; from {module} import {base}; sys.exit({call}())"
            (scripts / (entry.name + ".cmd")).write_text(
                '@echo off\n"%~dp0..\\python.exe" -c "' + code + '" %*\n', encoding="utf-8")


def scan(destination: Path, app_names: set[str]) -> list[dict]:
    secrets = local_secrets(REPO)
    matcher = re.compile(b"|".join(re.escape(value) for value in secrets)) if secrets else None
    overlap = max((len(value) for value in secrets), default=1) - 1
    inventory = []
    for index, file in enumerate(files(destination)):
        name = file.relative_to(destination).as_posix()
        if name == "PORTABLE_FILES.json":
            continue
        lowered = name.lower()
        if lowered.startswith(("api/", "assets/", "output/", ".git/")) or \
                file.name.lower() in {".env", "global_config.json", "history.json"}:
            raise RuntimeError("Private path in distribution: " + name)
        if lowered.startswith("data/") and not lowered.startswith("data/motion_models/"):
            raise RuntimeError("Unapproved data path: " + name)
        checksum = hashlib.sha256()
        tail = b""
        with file.open("rb") as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                checksum.update(chunk)
                data = tail + chunk
                if matcher and matcher.search(data):
                    raise RuntimeError("Local secret detected in " + name)
                if name in app_names and any(pattern.search(data) for pattern in TOKEN_PATTERNS):
                    raise RuntimeError("Credential pattern detected in " + name)
                tail = data[-max(overlap, 256):]
        inventory.append({"path": name, "bytes": file.stat().st_size, "sha256": checksum.hexdigest()})
        if index and index % 4000 == 0:
            print(f"Audited {index} files (secret values never printed)", flush=True)
    return inventory


def assemble(stage: Path, app_zip: Path, destination: Path) -> None:
    if destination.exists():
        raise RuntimeError("Destination already exists; never overwrite a used portable installation")
    if digest(app_zip) != PUBLIC_SOURCE_ZIP_SHA:
        raise RuntimeError("The application source ZIP is not the audited baseline")
    if Path(sys.executable).resolve() != (stage / "python/python.exe").resolve():
        raise RuntimeError("Use the staged Python to record the correct package versions")
    os.environ["PATH"] = str(stage / "bin/git/cmd") + os.pathsep + os.environ.get("PATH", "")
    ensure_motion_assets(stage / "data", lambda *_: None, lambda: False)
    destination.mkdir(parents=True)
    app_names = set()
    with zipfile.ZipFile(app_zip) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            # The clean source ZIP has a single top-level app directory.
            parts = Path(info.filename).parts
            name = Path(*parts[1:]).as_posix()
            if not name or private_path(name) or ".." in Path(name).parts:
                raise RuntimeError("Unexpected file in the audited application ZIP")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            app_names.add(name)
    for name in OVERLAYS:
        shutil.copy2(REPO / name, destination / name)
        app_names.add(name)
    for file in files(REPO / "tools/portable"):
        name = file.relative_to(REPO).as_posix()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        app_names.add(name)
    for folder in VENDOR_ROOTS:
        copy_tree(stage / folder, destination / folder, python=folder == "python")
    model_dir = destination / "data/motion_models"
    model_dir.mkdir(parents=True)
    for model in MODEL_ARTIFACTS:
        source = stage / "data/motion_models" / model.filename
        if digest(source) != model.sha256:
            raise RuntimeError("Model integrity check failed")
        shutil.copy2(source, model_dir / model.filename)
    for source in GIT_SOURCES:
        checkout = model_dir / "sources" / source.name
        copy_tree(stage / "data/motion_models/sources" / source.name, checkout)
        # Copying changes index stat data. Refresh before pruning sparse paths;
        # otherwise Git conservatively leaves unnecessary long example paths.
        git_env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        git_command = [str(stage / "bin/git/cmd/git.exe"), "-C", str(checkout), "-c", "core.longpaths=true"]
        subprocess.run(git_command + ["update-index", "--refresh"], env=git_env, check=True, capture_output=True)
        if source.name == "dwpose":
            subprocess.run(git_command + ["sparse-checkout", "reapply"], env=git_env, check=True, capture_output=True)
    install_console_wrappers(destination)
    shutil.copy2(stage / "InfiniteCanvas.exe", destination / "InfiniteCanvas.exe")
    shutil.copy2(REPO / "tools/portable/README.md", destination / "免安装使用说明.md")
    shutil.copy2(REPO / "tools/portable/THIRD_PARTY_NOTICES.md", destination / "THIRD_PARTY_NOTICES.md")
    packages = sorted([{"name": d.metadata["Name"], "version": d.version}
                       for d in runtime_distributions(destination)], key=lambda d: d["name"].lower())
    json_file(destination / "PORTABLE_PYTHON_PACKAGES.json", packages)
    components = json.loads((REPO / "tools/portable/components.json").read_text(encoding="utf-8"))
    components["application_base_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    components["application_local_overlays"] = list(OVERLAYS) + ["tools/portable/**"]
    components["public_models"] = [model.__dict__ for model in MODEL_ARTIFACTS]
    components["public_model_sources"] = [source.__dict__ for source in GIT_SOURCES]
    json_file(destination / "PORTABLE_COMPONENTS.json", components)
    app_names.update({"InfiniteCanvas.exe", "免安装使用说明.md", "THIRD_PARTY_NOTICES.md",
                      "PORTABLE_COMPONENTS.json", "PORTABLE_PYTHON_PACKAGES.json"})
    inventory = scan(destination, app_names)
    json_file(destination / "PORTABLE_FILES.json", inventory)
    print(f"ASSEMBLED: {len(inventory)} files, {sum(f['bytes'] for f in inventory)} bytes; privacy audit passed.", flush=True)


def seal(destination: Path, archive_path: Path) -> None:
    if archive_path.exists():
        raise RuntimeError("Archive exists; refusing to overwrite")
    inventory = json.loads((destination / "PORTABLE_FILES.json").read_text(encoding="utf-8"))
    paths = [item["path"] for item in inventory] + ["PORTABLE_FILES.json"]
    expected = {item["path"]: item["sha256"] for item in inventory}
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
        for index, name in enumerate(paths):
            path = destination / name
            if name in expected and digest(path) != expected[name]:
                raise RuntimeError("Sealed file changed: " + name)
            archive.write(path, destination.name + "/" + name)
            if index and index % 4000 == 0:
                print(f"Packed {index} files", flush=True)
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip():
            raise RuntimeError("ZIP CRC check failed")
    checksum = digest(archive_path)
    archive_path.with_suffix(".zip.sha256").write_text(checksum + "  " + archive_path.name + "\n", encoding="ascii")
    print(f"ZIP_OK: {archive_path.stat().st_size} bytes; SHA256 {checksum}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--app-zip", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--seal", type=Path, help="Only seal an already audited directory")
    args = parser.parse_args()
    if args.seal:
        seal(args.destination.resolve(), args.seal.resolve())
    else:
        assemble(args.stage.resolve(), args.app_zip.resolve(), args.destination.resolve())
