"""Make the README's screenshots from the last `scripts/check_live_drive.py` run.

check_live_drive.py drives the real console in headless Chrome on the tests' synthetic room (not a
scan) and a fake rover, and saves what it saw to build/live_check. This crops those captures into
assets/: the mission map, the train and optimize stages, and the 3D view after a drive.

    python scripts/check_live_drive.py      # orchestrator :8000 and dashboard :5173 running
    python scripts/readme_screenshots.py

The crop boxes match the console's layout at the check's 1440 px width; look at the output after a
layout change.
"""
import os
import shutil
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(ROOT, "build", "live_check")
ASSETS = os.path.join(ROOT, "assets")

# (left, top, right, bottom) in the full-page console capture.
CROPS = {
    "console-mission-map.png": (640, 500, 1344, 1310),
    "console-mission-train.png": (60, 1930, 620, 3070),
}


def main() -> None:
    page = os.path.join(LIVE, "console_map.png")
    drive = os.path.join(LIVE, "3d_after_drive.png")
    missing = [p for p in (page, drive) if not os.path.exists(p)]
    if missing:
        sys.exit(f"run scripts/check_live_drive.py first; missing {missing}")
    with Image.open(page) as full:
        for name, box in CROPS.items():
            full.crop(box).save(os.path.join(ASSETS, name))
            print(f"wrote assets/{name}")
    shutil.copyfile(drive, os.path.join(ASSETS, "console-3d-drive.png"))
    print("wrote assets/console-3d-drive.png")


if __name__ == "__main__":
    main()
