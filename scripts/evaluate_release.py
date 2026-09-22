"""Evaluate float and actual exported int8 bytes on the fixed release acceptance suite."""
import argparse
import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.artifact_validation import checksum  # noqa: E402
from policy.finetune.train_bc import LinearPolicy  # noqa: E402
from policy.release_evaluation import evaluate_release  # noqa: E402
from robot.policy_runner import load_int8_policy  # noqa: E402
from robot.spec import load_spec  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--spec", default="rover")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = evaluate_release(LinearPolicy.load(args.policy), load_int8_policy(args.artifact),
                              load_spec(args.spec))
    report.update(policy_sha256=checksum(args.policy), artifact_sha256=checksum(args.artifact),
                  recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(f"{report['gate_version']}: {'PASS' if report['passed'] else 'FAIL'}; {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
