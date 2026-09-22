"""Fail when application source exists locally but is hidden from Git."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "MOBILE-APP/app/src"
REQUIRED = (
    "ThemePreference.kt", "OrchestratorPreference.kt",
    "library/LibraryEntry.kt", "library/LibraryDao.kt",
    "library/LibraryDatabase.kt", "library/LibraryRepository.kt",
)


def main() -> int:
    data = SOURCE / "main/java/com/scanborn/ai/data"
    missing = [str(data / name) for name in REQUIRED if not (data / name).is_file()]
    paths = [p.relative_to(ROOT).as_posix() for p in SOURCE.rglob("*")
             if p.is_file() and p.suffix in {".kt", ".java", ".xml"}]
    checked = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"], cwd=ROOT,
        input="\n".join(paths), text=True, capture_output=True,
    )
    if checked.returncode not in (0, 1):
        print(checked.stderr, file=sys.stderr)
        return 1
    ignored = checked.stdout.splitlines()
    if missing or ignored:
        print("Missing required source:\n" + "\n".join(missing), file=sys.stderr)
        print("Ignored application source:\n" + "\n".join(ignored), file=sys.stderr)
        return 1
    print(f"Source completeness: {len(paths)} application files, none ignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
