"""Run the movable backend with its own log handles, independent of the GUI."""
from pathlib import Path
import os
import runpy
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    sys.path.insert(0, str(root))
    (root / "data").mkdir(exist_ok=True)
    # Never inherit GUI pipes: choosing to leave the server running must not
    # break its stdout when the launcher window exits.
    with (root / "data/portable-backend.log").open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = log
        sys.stderr = log
        try:
            runpy.run_path(str(root / "main.py"), run_name="__main__")
        except Exception:
            import traceback
            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
