"""Split an audited portable ZIP into two GitHub-sized assets without user data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

PART_SIZE = 1_950_000_000  # Under GitHub's per-asset 2 GiB limit.
ARCHIVE_NAME = "InfiniteCanvas-GT-Full.zip"


def split_archive(archive, destination, *, part_size=PART_SIZE):
    archive, destination = Path(archive), Path(destination)
    if archive.name != ARCHIVE_NAME or not part_size < archive.stat().st_size <= part_size * 2:
        raise ValueError("Expected the complete portable ZIP, with a size requiring two parts")
    destination.mkdir(parents=True, exist_ok=False)
    parts = []
    full_hash = hashlib.sha256()
    with archive.open("rb") as source:
        for index in (1, 2):
            path = destination / f"{ARCHIVE_NAME}.{index:03d}"
            remaining = part_size
            part_hash = hashlib.sha256()
            with path.open("xb") as output:
                while remaining and (chunk := source.read(min(4 * 1024 * 1024, remaining))):
                    output.write(chunk)
                    part_hash.update(chunk)
                    full_hash.update(chunk)
                    remaining -= len(chunk)
            parts.append({"name": path.name, "bytes": path.stat().st_size, "sha256": part_hash.hexdigest()})
            print(f"Prepared part {index}/2 ({path.stat().st_size} bytes)", flush=True)
        if source.read(1):
            raise ValueError("Archive grew during packaging")
    manifest = {"format": 1, "name": ARCHIVE_NAME, "bytes": archive.stat().st_size,
                "sha256": full_hash.hexdigest(), "parts": parts}
    (destination / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (destination / (ARCHIVE_NAME + ".sha256")).write_text(full_hash.hexdigest() + "  " + ARCHIVE_NAME + "\n", encoding="ascii")
    for name in ("Merge-Bundle.cmd", "Merge-Bundle.ps1", "INSTALL.md"):
        shutil.copyfile(Path(__file__).with_name(name), destination / name)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    split_archive(args.archive, args.destination)
