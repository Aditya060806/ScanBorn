"""Upload the tests' synthetic room to a running orchestrator, for checking the dashboard.

Prints two scan ids: the room with its table, and the same room with the table removed.
Type the first into the console's Capture "Scan id" field and run the stages from the
browser; use the second for Rescan. The room comes from tests/test_orchestrator.py, so the
dashboard is exercised on exactly what the test suite covers.

    uvicorn orchestrator.service:app --port 8000
    python scripts/seed_synthetic_scans.py [--url http://localhost:8000]
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic scans into an orchestrator.")
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    sys.path.insert(0, ROOT)
    import httpx

    from tests.test_orchestrator import frame_bytes, scan_frame

    def upload(frame: dict) -> str:
        first = httpx.post(f"{args.url}/capture", params={"index": 0},
                           files={"file": ("frame.npz", frame_bytes(frame))}, timeout=60)
        first.raise_for_status()
        scan_id = first.json()["scan_id"]
        httpx.post(f"{args.url}/capture/{scan_id}/complete", timeout=60).raise_for_status()
        return scan_id

    print("scan with table:  ", upload(scan_frame()))
    print("rescan, no table: ", upload(scan_frame(table=False)))


if __name__ == "__main__":
    main()
