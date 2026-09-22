"""Run the orchestrator on throwaway data for browser checks, never on the owner's data/.

    python scripts/serve_isolated.py [folder] [--port 8000]

The folder (default build/browser-check) holds the database and every scan, mesh and artifact
the run writes; delete it afterwards. The environment is set before the orchestrator is
imported, because orchestrator/db.py reads SCANBORN_DB at import time.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", nargs="?", default=os.path.join("build", "browser-check"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    root = os.path.abspath(args.folder)
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    os.environ["SCANBORN_DATA"] = os.path.join(root, "data")
    os.environ["SCANBORN_DB"] = os.path.join(root, "scanborn.db")
    print(f"isolated orchestrator: data {os.environ['SCANBORN_DATA']}", flush=True)

    import uvicorn
    uvicorn.run("orchestrator.service:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
