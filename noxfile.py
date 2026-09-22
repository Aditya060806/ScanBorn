"""Cross-platform task runner.

The Makefile is the canonical entry point on POSIX, but it is POSIX-only: `make` is not
present on a stock Windows box, and `make clean` shells out to `rm -rf` and `find`. Since
this project is developed on Windows and deployed to Linux containers, the same tasks need
a runner that works in both places.

`nox` and `tox` were already declared in requirements-dev.txt with no noxfile.py or
tox.ini anywhere in the repo — a dependency nobody could use. This file makes the nox half
of that real and keeps the task list in step with the Makefile's PKGS.

    nox -s lint          flake8 + mypy
    nox -s format        black + isort, in place
    nox -s format_check  black + isort, check only
    nox -s tests         pytest with coverage (pyproject addopts)
    nox -s security      bandit
    nox -s firmware      compile the ESP32 rover firmware with arduino-cli
    nox -s firmware_host build and run the firmware's math self-test on this machine
    nox -s firmware_calibration_host test the production calibration state machine with host stubs
    nox -s firmware_safety_host test sensor faults through the production safety code
    nox -s firmware_qemu boot the SELFTEST build on the emulated ESP32 and require PASS

`firmware` needs arduino-cli with the esp32:esp32 3.x core and the libraries listed in
docs/superpowers/plans/2026-09-14-phase-4-rover-protocol.md (step 4.1). When the Arduino data
directory is not the default one, point SCANBORN_ARDUINO_DATA at it (this project's Windows box:
D:/Arduino15). `firmware_host` needs a C++14 g++ (SCANBORN_CXX to choose one).

Sessions run in the *current* interpreter (`python=False`) rather than building a venv per
session. That is deliberate: this is a task runner, not a version matrix. CI already tests
3.10/3.11/3.12 via the GitHub Actions matrix, and rebuilding a venv here would reinstall
numpy and fastapi on every lint.
"""
import os
import shutil

import nox

# Mirrors PKGS in the Makefile. Keep the two in step.
PKGS = [
    "sdk/", "orchestrator/", "capture/", "reconstruction/", "semantic/",
    "robot/", "sarvam/", "twin/", "policy/", "deployment/", "tests/",
]

nox.options.sessions = ["lint", "tests"]
nox.options.reuse_existing_virtualenvs = True


@nox.session(python=False)
def lint(session: nox.Session) -> None:
    """flake8 then mypy, both reading their config from the repo root."""
    session.run("python", "-m", "flake8", *PKGS)
    session.run("python", "-m", "mypy", *PKGS)


@nox.session(python=False)
def format(session: nox.Session) -> None:
    """Rewrite in place. Note: the tree has never been black-formatted — see format_check."""
    session.run("python", "-m", "black", *PKGS)
    session.run("python", "-m", "isort", *PKGS)


@nox.session(python=False)
def format_check(session: nox.Session) -> None:
    """Check only.

    Expected to FAIL today. `format-check` is commented out in .github/workflows/ci.yml
    because the tree predates black and reformatting it would bury the real history in a
    whitespace commit. Run `nox -s format` deliberately, as its own commit, before turning
    this on in CI.
    """
    session.run("python", "-m", "black", "--check", *PKGS)
    session.run("python", "-m", "isort", "--check-only", *PKGS)


@nox.session(python=False)
def tests(session: nox.Session) -> None:
    """Full suite. Coverage flags come from [tool.pytest.ini_options] addopts."""
    session.run("python", "-m", "pytest", *session.posargs)


@nox.session(python=False)
def security(session: nox.Session) -> None:
    """bandit over the shipped packages, matching the CI security job (tests/ excluded)."""
    session.run("python", "-m", "bandit", "-r", "-ll",
                *[p for p in PKGS if p != "tests/"])


FIRMWARE_SKETCH = "firmware/scanborn_rover"
# A generic ESP32 DevKit (ESP32-WROOM-32), with DIO flash: every WROOM module supports it, and it is
# the mode Espressif's QEMU boots (QIO fails there in flash initialisation), so the image checked in
# the emulator is the image that gets flashed.
FIRMWARE_FQBN = "esp32:esp32:esp32:FlashMode=dio"
QEMU = os.environ.get("SCANBORN_QEMU", "qemu-system-xtensa")
HOST_SELFTEST = "firmware/tests/host_selftest.cpp"
HOST_BUILD = os.path.join("build", "firmware_host")
ESP32_INDEX = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"


def _arduino_env() -> dict:
    """arduino-cli's directories: its defaults, or the tree under SCANBORN_ARDUINO_DATA."""
    env = {"ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS": ESP32_INDEX}
    data = os.environ.get("SCANBORN_ARDUINO_DATA")
    if data:
        env.update({"ARDUINO_DIRECTORIES_DATA": data,
                    "ARDUINO_DIRECTORIES_DOWNLOADS": os.path.join(data, "staging"),
                    "ARDUINO_DIRECTORIES_USER": os.path.join(data, "user")})
    return env


@nox.session(python=False)
def firmware(session: nox.Session) -> None:
    """Compile the rover firmware for the ESP32, the driving build and the SELFTEST build, after
    regenerating the golden vectors. Each build keeps its own folder under build/firmware/."""
    if shutil.which("arduino-cli") is None:
        session.error("arduino-cli is not on PATH (phase 4 plan, step 4.1)")
    session.run("python", "scripts/gen_rover_golden.py")
    for name, defines in (("rover", ""), ("selftest", "-DSELFTEST")):
        args = ["arduino-cli", "compile", "--fqbn", FIRMWARE_FQBN, "--warnings", "default",
                "--build-path", os.path.join("build", "firmware", name)]
        if defines:
            args += ["--build-property", f"compiler.cpp.extra_flags={defines}"]
        session.run(*args, FIRMWARE_SKETCH, env=_arduino_env(), external=True)


@nox.session(python=False)
def firmware_host(session: nox.Session) -> None:
    """The firmware's controller and odometry math, built for this machine and checked against
    tests/fixtures/rover_golden.json (through the generated golden.h).

    -msse2 -mfpmath=sse keep a 32-bit x86 compiler off x87 80-bit arithmetic, so doubles round
    the way they do on the ESP32 and in numpy.
    """
    compiler = os.environ.get("SCANBORN_CXX", "g++")
    if shutil.which(compiler) is None:
        session.error(f"{compiler} is not on PATH; set SCANBORN_CXX to a C++14 compiler")
    session.run("python", "scripts/gen_rover_golden.py")
    os.makedirs(HOST_BUILD, exist_ok=True)
    binary = os.path.join(HOST_BUILD, "host_selftest.exe" if os.name == "nt" else "host_selftest")
    session.run(compiler, "-std=c++14", "-O1", "-Wall", "-Wextra", "-msse2", "-mfpmath=sse",
                "-I", FIRMWARE_SKETCH, HOST_SELFTEST, "-o", binary, external=True)
    session.run(binary, external=True)


@nox.session(python=False)
def firmware_safety_host(session: nox.Session) -> None:
    """Production sensor safety checks with deterministic Arduino/sensor IO."""
    compiler = os.environ.get("SCANBORN_CXX", "g++")
    os.makedirs(HOST_BUILD, exist_ok=True)
    binary = os.path.join(HOST_BUILD, "safety.exe" if os.name == "nt" else "safety")
    session.run(compiler, "-std=c++14", "-Wall", "-Wextra", "-msse2", "-mfpmath=sse",
                "-I", "firmware/tests/safety_stubs", "-I", FIRMWARE_SKETCH,
                "firmware/tests/safety_host_test.cpp", "firmware/scanborn_rover/safety.cpp",
                "-o", binary, external=True)
    session.run(binary, external=True)


@nox.session(python=False)
def firmware_calibration_host(session: nox.Session) -> None:
    """Production calibration state machine with a deterministic hardware boundary."""
    compiler = os.environ.get("SCANBORN_CXX", "g++")
    data = os.environ.get("SCANBORN_ARDUINO_DATA", "")
    headers = os.environ.get("SCANBORN_ARDUINOJSON",
                             os.path.join(data, "user", "libraries", "ArduinoJson", "src"))
    if not os.path.isfile(os.path.join(headers, "ArduinoJson.h")):
        session.error("Set SCANBORN_ARDUINOJSON to the installed ArduinoJson src directory")
    os.makedirs(HOST_BUILD, exist_ok=True)
    binary = os.path.join(HOST_BUILD, "calibration.exe" if os.name == "nt" else "calibration")
    session.run(compiler, "-std=c++14", "-Wall", "-Wextra", "-msse2", "-mfpmath=sse",
                "-DCALIBRATION_HOST_TEST",
                "-I", headers, "-I", FIRMWARE_SKETCH, "-I", "firmware/tests",
                "firmware/tests/calibration_host_test.cpp", "firmware/scanborn_rover/calibrate.cpp",
                "-o", binary, external=True)
    session.run(binary, external=True)


def _boot_on_qemu(qemu: str, image: str, log_name: str, done, timeout_s: float = 90) -> str:
    """Boot a merged flash image on the emulated ESP32 until `done(serial_text)` or the timeout;
    returns the serial output."""
    import subprocess
    import time

    roms = os.path.join(os.path.dirname(os.path.dirname(qemu)), "share", "qemu")
    log = os.path.abspath(os.path.join("build", "firmware", log_name))
    if os.path.exists(log):
        os.remove(log)
    process = subprocess.Popen(
        # Instruction-count timing avoids an idle-task cache panic seen with the
        # 3.3.11 Arduino core on QEMU 9.2.2 under wall-clock scheduling. These are
        # functional checks, never real-time performance measurements.
        [qemu, "-nographic", "-machine", "esp32", "-L", roms, "-icount", "3",
         "-drive", f"file={image},if=mtd,format=raw,snapshot=on",
         "-serial", "file:" + log.replace("\\", "/"), "-monitor", "none"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    output = ""
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if os.path.exists(log):
                with open(log, encoding="utf-8", errors="replace") as f:
                    output = f.read().replace("\r", "")
                if done(output):
                    break
    finally:
        process.kill()
        process.wait()
    return output


@nox.session(python=False)
def firmware_qemu(session: nox.Session) -> None:
    """Both firmware images on Espressif's ESP32 emulator, after `nox -s firmware`.

    1. The SELFTEST image must print PASS: the golden vectors hold on the ESP32's own compiler,
       libraries and CPU model, not just on the laptop.
    2. The driving image must boot to the point the emulator can reach: its banner, flash
       settings, and a clear refusal to drive without the IMU and the ToF sensor (the emulator has
       neither), with no error before it. It then stops in WiFi's radio start-up, which the
       emulator cannot provide; networking, motors and sensors are Phase 6 on the real board.

    Needs Espressif's QEMU build: SCANBORN_QEMU points at qemu-system-xtensa (this project's
    Windows box: D:/Tools/esp-qemu/qemu/bin/).
    """
    qemu = QEMU if os.path.isfile(QEMU) else shutil.which(QEMU)
    if not qemu:
        session.error("qemu-system-xtensa not found; set SCANBORN_QEMU (Espressif's QEMU build)")
    images = {name: os.path.join("build", "firmware", name, "scanborn_rover.ino.merged.bin")
              for name in ("selftest", "rover")}
    for image in images.values():
        if not os.path.isfile(image):
            session.error(f"{image} is missing: run nox -s firmware first")

    def lines(text: str) -> list:
        return [line.strip() for line in text.split("\n")]

    output = _boot_on_qemu(qemu, images["selftest"], "qemu_selftest.log",
                           lambda text: "PASS" in lines(text) or "FAIL" in lines(text))
    session.log(output[-400:])
    if "PASS" not in lines(output):
        session.error("the SELFTEST image did not print PASS on the emulated ESP32")

    output = _boot_on_qemu(qemu, images["rover"], "qemu_rover.log",
                           lambda text: "gyro bias not measured: no IMU" in text, timeout_s=60)
    boot = output.split("gyro bias not measured: no IMU")[0]
    session.log(output[-800:])
    expected = ["ScanBorn rover, firmware",
                "IMU (MPU6050) not found: the rover will refuse to drive",
                "ToF (VL53L0X) not found: the rover will refuse to drive"]
    missing = [text for text in expected if text not in boot]
    errors = [line for line in lines(boot) if line.startswith("E (") or "assert failed" in line]
    if missing or "gyro bias not measured: no IMU" not in output:
        session.error(f"the driving image did not boot as expected; missing: {missing}")
    if errors:
        session.error(f"the driving image logged errors while booting: {errors}")
