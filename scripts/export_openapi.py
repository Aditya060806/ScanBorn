"""Write docs/api/openapi.yaml from the orchestrator's own schema.

The document used to be written by hand and described routes that never existed. Generating
it from `app.openapi()` keeps it true; tests/test_openapi.py fails when a route is added
without re-running this.

    python scripts/export_openapi.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "api", "openapi.yaml")
HEADER = ("# Generated from orchestrator/service.py by `python scripts/export_openapi.py`.\n"
          "# Do not edit by hand: tests/test_openapi.py checks it lists every route.\n")


def main() -> None:
    sys.path.insert(0, ROOT)
    import yaml

    from orchestrator.service import app

    spec = app.openapi()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER)
        yaml.safe_dump(spec, f, sort_keys=False, allow_unicode=True, width=100)
    operations = sum(len(item) for item in spec["paths"].values())
    print(f"wrote {OUT}: {len(spec['paths'])} paths, {operations} operations")


if __name__ == "__main__":
    main()
