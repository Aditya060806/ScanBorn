"""Score one planner on a frozen intent set and write the report to benchmarks/.

    python scripts/evaluate_intents.py                         # the local keyword planner
    python scripts/evaluate_intents.py --provider groq --allow-remote

A remote provider is paid and sends every instruction off this machine, so it runs only with
--allow-remote and its key configured. The report publishes semantic success and abstention per
category; it never treats a valid-looking graph as a correct one. Exits 1 when the safety
invariant (no wrong plan, no plan where a refusal was expected) fails.
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    sys.path.insert(0, ROOT)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    from sarvam.task_engine.intent_eval import evaluate
    from sarvam.task_engine.provider import PLANNER_PROVIDERS, REMOTE_PROVIDERS

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--provider", default="local", choices=PLANNER_PROVIDERS)
    parser.add_argument("--set", default="v1", dest="version")
    parser.add_argument("--allow-remote", action="store_true",
                        help="permit a remote, paid provider to receive every instruction")
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.provider in REMOTE_PROVIDERS and not args.allow_remote:
        parser.error(f"{args.provider} is remote and paid; pass --allow-remote to run it")

    report = evaluate(args.provider, args.version)
    report.update(recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"), command=" ".join(["python", "scripts/evaluate_intents.py",
                                               *sys.argv[1:]]))
    out = args.out or os.path.join(ROOT, "benchmarks",
                                   f"intents-{args.provider}-{args.version}.json")
    with open(out, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")

    print(f"{args.provider} on intent set {args.version}: {report['totals']}")
    for name, entry in report["categories"].items():
        print(f"  {name:13} cases {entry['cases']:2}  semantic success "
              f"{entry['semantic_success']}  abstention {entry['abstention']}  "
              f"refusal accuracy {entry['refusal_accuracy']}")
    print(f"safety invariant holds: {report['safety_invariant_holds']}  -> {out}")
    return 0 if report["safety_invariant_holds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
