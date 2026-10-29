"""GT-only, commit-pinned program updates. Never read/upload personal data."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

REPOSITORY = "moretime07/Infinite-Canvas-GT"
BRANCH = "main"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
RAW_ROOT = f"https://raw.githubusercontent.com/{REPOSITORY}"
REQUIRED = {"main.py", "VERSION", "requirements.txt", "project_updater.py",
            "static/index.html", "tools/restart_backend.py"}
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


def allowed_file(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return False
    parts = name.split("/")
    if any(not part or part in {".", ".."} or part.endswith((".", " "))
           or part.startswith(".") or any(ord(c) < 32 for c in part) for part in parts):
        return False
    if any(re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".", 1)[0], re.I) for part in parts):
        return False
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in {".key", ".pem", ".log", ".sqlite", ".sqlite3", ".bak"}:
        return False
    if len(parts) == 1:
        return name in {"VERSION", "README.md", "LICENSE", "requirements.txt", "requirements-motion.txt"} or suffix in {".py", ".bat", ".command"}
    return ((parts[0] == "static" and suffix in {".html", ".css", ".js", ".json", ".svg", ".png", ".jpg", ".ico", ".woff", ".woff2", ".md"})
            or (parts[0] in {"tools", "motion_extractor"} and suffix in {".py", ".ps1"})
            or (parts[0] == "workflows" and suffix == ".json"))


def inside(root, relative):
    root = Path(root).resolve()
    target = root.joinpath(*relative.split("/"))
    if not target.resolve().is_relative_to(root) or target.resolve() == root:
        raise ValueError("Unsafe update path")
    for item in (target, *target.parents):
        if item == root:
            break
        if item.is_symlink():
            raise ValueError("Update paths must not contain symlinks")
    return target


def code_path(root, name):
    if not allowed_file(name):
        raise ValueError("File is outside the program update allowlist")
    return inside(root, name)


def read_remote(url, limit=MAX_FILE):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "raw.githubusercontent.com"}:
        raise ValueError("Untrusted update host")
    request = urllib.request.Request(url, headers={"User-Agent": "Infinite-Canvas-GT-Updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        if urllib.parse.urlsplit(response.url).hostname not in {"api.github.com", "raw.githubusercontent.com"}:
            raise ValueError("Unexpected update redirect")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Update file exceeds size limit")
    return data


def download_snapshot(stage, reader=read_remote):
    commit = json.loads(reader(f"{API_ROOT}/commits/{BRANCH}"))["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid GitHub commit")
    tree = json.loads(reader(f"{API_ROOT}/git/trees/{commit}?recursive=1"))
    if tree.get("truncated"):
        raise ValueError("Incomplete GitHub file list")
    entries = [item for item in tree.get("tree", []) if item.get("type") == "blob" and allowed_file(item.get("path"))]
    paths = [item["path"] for item in entries]
    if not REQUIRED.issubset(paths) or len(set(p.casefold() for p in paths)) != len(paths):
        raise ValueError("Update snapshot is incomplete or has conflicting paths")
    if sum(int(item.get("size", 0)) for item in entries) > MAX_TOTAL:
        raise ValueError("Update snapshot is too large")

    def download(item):
        if item.get("mode") not in {"100644", "100755"}:
            raise ValueError("Linked files are not allowed in updates")
        data = reader(f"{RAW_ROOT}/{commit}/{urllib.parse.quote(item['path'], safe='/')}")
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != item.get("sha") or len(data) != item.get("size") or len(data) > MAX_FILE:
            raise ValueError("Update file integrity check failed")
        target = code_path(stage, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if os.name != "nt" and item["mode"] == "100755":
            target.chmod(0o755)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(download, entries))
    return commit, paths


@contextlib.contextmanager
def local_lock(root):
    target = inside(root, "data/update.lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".update-" + uuid.uuid4().hex)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def apply_files(root, stage, files, commit="", remove=()):
    """Back up code only; on failure restore replaced files and remove additions."""
    names = sorted(set(files) | set(remove), key=lambda name: (name == "VERSION", name))
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Conflicting update paths")
    backup = inside(root, "data/update_backups/" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    records = []
    for name in names:
        target = code_path(root, name)
        if target.exists() and not target.is_file():
            raise ValueError("A program file path is occupied by a directory")
        source = None if name in remove else code_path(stage, name)
        if source is not None and not source.is_file():
            raise ValueError("Staged program file is missing")
        record = {"path": name, "existed": target.is_file(), "sha256": sha256(target) if target.is_file() else None}
        records.append(record)
        if record["existed"]:
            copy = code_path(backup, name)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, copy)
    backup.mkdir(parents=True, exist_ok=True)
    manifest = {"format": 1, "commit": commit, "files": records}
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    changed = []
    try:
        for record in records:
            target = code_path(root, record["path"])
            if record["path"] in remove:
                target.unlink(missing_ok=True)
            else:
                atomic_copy(code_path(stage, record["path"]), target)
            changed.append(record)
    except Exception:
        for record in reversed(changed):
            target = code_path(root, record["path"])
            if record["existed"]:
                atomic_copy(code_path(backup, record["path"]), target)
            else:
                target.unlink(missing_ok=True)
        raise
    return {"updated": names, "count": len(names), "backup_dir": str(backup), "commit": commit}


def prepare_runtime(root, stage, python=sys.executable):
    # No GPU/model reinstall and no forced upgrades of already-satisfied packages.
    old = code_path(root, "requirements.txt")
    new = code_path(stage, "requirements.txt")
    changed = not old.exists() or old.read_bytes() != new.read_bytes()
    if changed:
        result = subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(new)],
                                capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError("依赖安装失败，程序文件未替换。请运行安装依赖.bat 后重试。")
    for name in stage.rglob("*.py"):
        compile(name.read_bytes(), str(name), "exec")
    # Import in an isolated staging directory, never against the user's data.
    result = subprocess.run([python, "-B", "-c", "import main; assert main.app"], cwd=stage,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("新版启动检查失败，程序文件未替换。请运行安装依赖.bat 后重试。")


def install(root, python=sys.executable, downloader=download_snapshot, validator=prepare_runtime):
    root = Path(root).resolve()
    if not (root / "main.py").is_file():
        raise ValueError("请选择包含 main.py 的已安装项目目录")
    with local_lock(root):
        staging_parent = inside(root, "data/update_staging")
        staging_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gt-", dir=staging_parent) as directory:
            stage = Path(directory)
            commit, files = downloader(stage)
            version = (stage / "VERSION").read_text(encoding="utf-8-sig").strip()
            if not re.fullmatch(r"\d+(?:\.\d+){2,4}", version):
                raise ValueError("Invalid release version")
            validator(root, stage, python)
            result = apply_files(root, stage, files, commit)
            result["version"] = version
            return result


def rollback(root, name):
    if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", name):
        raise ValueError("请选择 GT 更新器创建的备份")
    with local_lock(root):
        backup = inside(root, "data/update_backups/" + name)
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != 1:
            raise ValueError("Unsupported backup format")
        files, remove = [], []
        for record in manifest["files"]:
            source = code_path(backup, record["path"])
            code_path(root, record["path"])
            if record["existed"]:
                if not source.is_file() or sha256(source) != record["sha256"]:
                    raise ValueError("Backup integrity check failed")
                files.append(record["path"])
            else:
                remove.append(record["path"])
        return apply_files(root, backup, files, remove=remove)


def cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True)
    args = parser.parse_args()
    # Bootstrap cannot safely stop an arbitrary process: require the user to close it.
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", 3000)) == 0:
            print("请先在旧服务窗口按 Ctrl+C 停止后台，再运行本工具。")
            return 1
    try:
        result = install(args.app_dir)
        print("更新成功：" + result["version"])
        print("程序备份：" + result["backup_dir"])
        print("密钥、画布、素材未改动。现在运行启动服务.bat；以后可点网页更新按钮。")
        return 0
    except Exception as exc:
        print("更新未完成：" + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
