"""Scan the Git index and unpublished commits without printing secret values.

Run before committing/pushing. This is a guardrail, not a guarantee that every
possible private item can be detected. Never use it instead of reviewing files.
"""
import argparse
import re
import subprocess
from pathlib import Path


PRIVATE_ROOTS = {"api", "data", "assets", "output", "tmp", "dist", "python", ".venv", ".codex", ".playwright-cli", "test-results"}
PRIVATE_NAMES = {"history.json", "global_config.json", "_self_restart.bat"}
PRIVATE_SUFFIXES = {".log", ".lnk", ".pem", ".key", ".p12", ".pfx", ".zip", ".7z", ".rar", ".sqlite", ".sqlite3"}
TOKEN_PATTERNS = [
    re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    re.compile(rb"\bsk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{24,}\b"),
    re.compile(rb"\bAKID[A-Za-z0-9]{28,}\b"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)


def private_path(name):
    parts = name.lower().split("/")
    return (
        parts[0] in PRIVATE_ROOTS or parts[-1] in PRIVATE_NAMES
        or parts[-1] == ".env" or parts[-1].startswith(".env.")
        or Path(name).suffix.lower() in PRIVATE_SUFFIXES
        or parts[0].startswith("final-fix-")
    )


def local_secrets(root):
    values = set()
    for path in (root / "API/.env", root / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if not sep or key.lstrip().startswith("#"):
                continue
            if not re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", key, re.I):
                continue
            value = value.strip().strip("\"'")
            if len(value) >= 8 and not re.match(r"(?i)^(test|example|your[-_ ]|placeholder|change[-_ ]?me)", value):
                values.add(value.encode())
    return values


def entries(ref=None):
    if ref:
        raw = git("ls-tree", "-r", "-z", ref)
    else:
        raw = git("ls-files", "--stage", "-z")
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, name = record.split(b"\t", 1)
        fields = meta.split()
        sha = fields[2] if ref else fields[1]
        yield name.decode("utf-8"), sha.decode("ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", help="Also scan commits not yet in this ref, e.g. origin/main")
    args = parser.parse_args()
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    secrets = local_secrets(root)
    refs = [("index", None)]
    if args.upstream:
        refs += [(sha[:12], sha) for sha in git("rev-list", f"{args.upstream}..HEAD").decode().splitlines()]
    seen = set()
    findings = set()
    for label, ref in refs:
        for name, sha in entries(ref):
            if private_path(name):
                findings.add((label, name, "private/runtime file"))
            if (name, sha) in seen:
                continue
            seen.add((name, sha))
            data = git("cat-file", "blob", sha)
            if any(secret in data for secret in secrets):
                findings.add((label, name, "matches a locally configured secret"))
            if any(pattern.search(data) for pattern in TOKEN_PATTERNS):
                findings.add((label, name, "credential/private-key pattern"))
    for label, name, reason in sorted(findings):
        print(f"BLOCKED [{label}] {name}: {reason}")
    print(f"Scanned {len(seen)} file versions across index and {len(refs)-1} unpublished commits; {len(findings)} findings. No secret values printed.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
