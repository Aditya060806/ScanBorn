"""Summarize complete operator measurement records for proposed A3/A8 accuracy gates."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot.accuracy_report import summarize  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output already exists; choose a new report path")
    report = summarize(json.loads(args.record.read_text(encoding="utf-8")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(f"{report['gate_version']}: "
          f"{report['within_target']}/{report['attempts']} within target; "
          f"provenance {report['provenance']}; {args.out}")
    return 0 if report["passed_declared_measurements"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
