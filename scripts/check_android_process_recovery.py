"""Force-stop pending recovery on an explicitly selected emulator, then verify in a new process.

Requires the debug and instrumentation APKs already installed. Never targets a physical phone.
"""
import argparse
from pathlib import Path
import re
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"emulator-\d+", args.serial):
        parser.error("This force-stop harness is restricted to an emulator serial")
    adb = ["adb", "-s", args.serial]
    device = subprocess.check_output(
        adb + ["shell", "getprop", "ro.kernel.qemu"], text=True).strip()
    if device != "1":
        parser.error("Selected device did not identify itself as an emulator")
    boot = subprocess.check_output(
        adb + ["shell", "getprop", "sys.boot_completed"], text=True).strip()
    if boot != "1":
        parser.error("Wait for the emulator to finish booting before running recovery checks")
    for package in ("com.scanborn.ai", "com.scanborn.ai.test"):
        installed = subprocess.check_output(
            adb + ["shell", "pm", "path", package], text=True).strip()
        if not installed.startswith("package:"):
            parser.error(f"Install the debug/instrumentation APK first: {package} is missing")
    args.out.mkdir(parents=True, exist_ok=True)
    for kind in ("deployment", "calibration", "stage", "capture"):
        token = str(uuid.uuid4())
        test_class = "CaptureProcessTest" if kind == "capture" else "RecoveryHttpTest"
        common = adb + ["shell", "am", "instrument", "-w", "-r", "-e", "class",
                        f"com.scanborn.ai.spaces.{test_class}#processDeathRecovery",
                        "-e", "recovery_token", token, "-e", "recovery_kind", kind]
        runner = "com.scanborn.ai.test/androidx.test.runner.AndroidJUnitRunner"
        pending_log = args.out / f"{kind}-interrupted.txt"
        with pending_log.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(common + ["-e", "recovery_phase", "prepare", runner],
                                       stdout=stream, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 45
                while f"scanborn_ready={token}" not in pending_log.read_text(encoding="utf-8"):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(f"Pending process did not become ready: {pending_log}")
                    time.sleep(0.1)
                subprocess.run(adb + ["shell", "am", "force-stop", "com.scanborn.ai"],
                               check=True, timeout=10)
                process.wait(timeout=15)
            finally:
                if process.poll() is None:
                    subprocess.run(adb + ["shell", "am", "force-stop", "com.scanborn.ai"],
                                   check=True, timeout=10)
                    process.kill()
                    process.wait(timeout=10)
        result = subprocess.run(common + ["-e", "recovery_phase", "recover", runner],
                                capture_output=True, text=True, timeout=60, check=True)
        (args.out / f"{kind}-recovered.txt").write_text(
            result.stdout + result.stderr, encoding="utf-8")
        if "OK (1 test)" not in result.stdout:
            raise RuntimeError(f"{kind} process recovery failed; inspect {args.out}")
        print(f"PASS {kind}: force-stopped pending process; new process verified recovery")


if __name__ == "__main__":
    main()
